from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import urllib.error

from deploy import opencode_health as health


BAD = {'observation': 'provider_503', 'global_http_status': 200, 'provider_http_status': 503}
GOOD = {'observation': 'healthy', 'global_http_status': 200, 'provider_http_status': 200}
UNKNOWN = {'observation': 'probe_unknown', 'global_http_status': 200, 'provider_http_status': 403}


class FakeRuntime:
    def __init__(self):
        self.probes = [BAD]
        self.reasons = [None]
        self.restarted = 0
        self.locked = False
        self.lock_busy = False
        self.restart_ok = True
        self.ready_value = GOOD
        self.before_restart = None
        self.safe_checks = 0

    @contextmanager
    def state_lock(self, path):
        yield

    @contextmanager
    def recovery_locks(self):
        if self.lock_busy:
            raise BlockingIOError('isolated busy lock')
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def probe(self):
        return self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]

    def safe(self):
        self.safe_checks += 1
        return self.reasons.pop(0) if len(self.reasons) > 1 else self.reasons[0]

    def restart(self):
        assert self.locked
        if self.before_restart:
            self.before_restart()
        self.restarted += 1
        return self.restart_ok

    def ready(self):
        assert self.locked
        return self.ready_value


class Monitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.runtime = FakeRuntime()

    def tick(self, clock, **kwargs):
        return health.tick(self.path, self.runtime, clock=clock, **kwargs)

    def seed(self, count=2, attempts=None, at=1000):
        state = health.initial_state()
        state.update(consecutive_provider_503=count, checked_at=at, restart_attempts=attempts or [])
        health.save_state(self.path, state)

    def test_three_consecutive_failures_restart_once_after_locked_recheck(self):
        self.assertEqual(self.tick(1000)['consecutive_provider_503'], 1)
        self.assertEqual(self.tick(1060)['consecutive_provider_503'], 2)
        self.assertEqual(self.runtime.restarted, 0)
        self.runtime.before_restart = lambda: self.assertEqual(
            json.loads(self.path.read_text())['last_restart_result'], 'attempted')
        result = self.tick(1120)
        self.assertEqual(result['outcome'], 'recovered')
        self.assertEqual(result['restart_attempts'], [1120])
        self.assertEqual(result['consecutive_provider_503'], 0)
        self.assertEqual(self.runtime.safe_checks, 2)
        self.assertEqual(self.runtime.restarted, 1)

    def test_healthy_unknown_and_non_503_interrupt_consecutive_failures(self):
        for observed in (GOOD, UNKNOWN, {'observation': 'global_unhealthy', 'global_http_status': 503}):
            self.seed()
            self.runtime.probes = [observed]
            self.assertEqual(self.tick(1060)['consecutive_provider_503'], 0)
            self.assertEqual(self.runtime.restarted, 0)

    def test_stale_samples_do_not_count_as_consecutive(self):
        self.seed(at=1000)
        self.assertEqual(self.tick(2000)['consecutive_provider_503'], 1)
        self.assertEqual(self.runtime.restarted, 0)

    def test_each_busy_or_unknown_safety_condition_blocks_restart(self):
        for reason in ('service_not_active', 'owner_busy', 'research_busy', 'sessions_busy',
                       'sessions_unknown', 'safety_unknown'):
            with self.subTest(reason=reason):
                self.seed()
                self.runtime.reasons = [reason]
                self.assertEqual(self.tick(1060)['outcome'], reason)
                self.assertEqual(self.runtime.restarted, 0)

    def test_new_work_between_first_check_and_lock_blocks_restart(self):
        self.seed()
        self.runtime.reasons = [None, 'owner_busy']
        self.assertEqual(self.tick(1060)['outcome'], 'owner_busy')
        self.assertEqual(self.runtime.safe_checks, 2)
        self.assertEqual(self.runtime.restarted, 0)

    def test_deployment_or_inference_lock_blocks_restart(self):
        self.seed()
        self.runtime.lock_busy = True
        self.assertEqual(self.tick(1060)['outcome'], 'recovery_lock_busy')
        self.assertEqual(self.runtime.restarted, 0)

    def test_provider_recovery_or_unknown_under_lock_blocks_restart(self):
        for new in (GOOD, UNKNOWN):
            self.seed()
            self.runtime.probes = [BAD, new]
            self.assertEqual(self.tick(1060)['outcome'], 'changed_before_restart')
            self.assertEqual(self.runtime.restarted, 0)

    def test_fifteen_minute_cooldown_and_two_per_hour_survive_state_reload(self):
        self.seed(at=1000, attempts=[1000])
        self.assertEqual(self.tick(1100)['outcome'], 'restart_cooldown')
        self.seed(at=2800, attempts=[1000, 1950])
        self.assertEqual(self.tick(2900)['outcome'], 'hourly_restart_limit')
        self.seed(at=4700, attempts=[1000, 1950])
        self.assertEqual(self.tick(4760)['outcome'], 'recovered')
        self.assertEqual(self.runtime.restarted, 1)
        self.assertEqual(json.loads(self.path.read_text())['restart_attempts'], [1950, 4760])

    def test_failed_restart_consumes_attempt_and_cooldown(self):
        self.seed()
        self.runtime.restart_ok = False
        result = self.tick(1060)
        self.assertEqual(result['outcome'], 'restart_failed')
        self.assertEqual(result['restart_attempts'], [1060])
        self.assertEqual(self.tick(1120)['consecutive_provider_503'], 1)
        self.tick(1180)
        self.assertEqual(self.tick(1240)['outcome'], 'restart_cooldown')
        self.assertEqual(self.runtime.restarted, 1)

    def test_restart_is_not_success_until_provider_health_is_confirmed(self):
        self.seed()
        self.runtime.ready_value = BAD
        self.assertEqual(self.tick(1060)['outcome'], 'restart_completed_unhealthy')
        self.assertEqual(json.loads(self.path.read_text())['recovery_events'], [])

    def test_durable_event_is_written_before_export_and_survives_healthy_ticks(self):
        self.seed()
        exports = []

        def exported(path, state):
            persisted = json.loads(self.path.read_text())
            self.assertEqual(persisted['recovery_events'], state['recovery_events'])
            exports.append(persisted)

        def before_restart():
            persisted = json.loads(self.path.read_text())
            self.assertRegex(persisted['last_restart_attempt']['id'],
                             r'^opencode:service_restart:[0-9a-f]{32}$')
            self.assertEqual(persisted['recovery_events'], [])

        self.runtime.before_restart = before_restart
        with patch.object(health, 'save_events', side_effect=exported):
            result = self.tick(1060, events_path=health.EVENTS)
            event = result['recovery_events'][0]
            self.assertEqual(event['id'], result['last_restart_attempt']['id'])
            self.assertEqual(event['proof']['attempted_at'], 1060)
            self.assertGreaterEqual(event['verified_at'], 1060)
            self.runtime.probes = [GOOD]
            after = self.tick(5000, events_path=health.EVENTS)
        self.assertEqual(after['outcome'], 'healthy')
        self.assertEqual(after['restart_attempts'], [])
        self.assertEqual(after['recovery_events'], [event])
        self.assertEqual(exports[-1]['recovery_events'], [event])
        self.assertEqual(self.runtime.restarted, 1)

    def test_export_failure_retries_without_restarting_or_losing_evidence(self):
        self.seed()
        with patch.object(health, 'save_events', side_effect=OSError('no export')):
            result = self.tick(1060, events_path=health.EVENTS)
        self.assertEqual(result['outcome'], 'recovered')
        self.assertTrue(result['event_export_failed'])
        event = json.loads(self.path.read_text())['recovery_events'][0]
        self.runtime.probes = [GOOD]
        with patch.object(health, 'save_events') as exporter:
            result = self.tick(1120, events_path=health.EVENTS)
        self.assertEqual(result['recovery_events'], [event])
        self.assertNotIn('event_export_failed', result)
        exporter.assert_called_once()
        self.assertEqual(self.runtime.restarted, 1)

    def test_failed_and_uncertain_attempts_never_become_events_on_later_health(self):
        for restart_ok, ready in ((False, GOOD), (True, BAD), (True, UNKNOWN)):
            self.seed()
            self.runtime.probes = [BAD]
            self.runtime.restart_ok = restart_ok
            self.runtime.ready_value = ready
            result = self.tick(1060)
            self.assertEqual(result['recovery_events'], [])
            self.runtime.probes = [GOOD]
            self.assertEqual(self.tick(1120)['recovery_events'], [])

    def test_events_are_bounded_and_do_not_retain_untrusted_fields(self):
        self.seed()
        event = self.tick(1060)['recovery_events'][0]
        values = [{**event, 'id': 'opencode:service_restart:' + format(index, '032x'),
                   'private_body': 'do-not-export',
                   'proof': {**event['proof'], 'token': 'do-not-export'}} for index in range(80)]
        cleaned = health.clean_events(values)
        self.assertEqual(len(cleaned), health.MAX_EVENTS)
        self.assertNotIn('do-not-export', json.dumps(cleaned))
        state = health.initial_state()
        state['recovery_events'] = cleaned
        health.save_state(self.path, state)
        self.assertEqual(health.load_state(self.path, 5000)['recovery_events'], cleaned)
        self.assertLess(self.path.stat().st_size, health.MAX_STATE)

    def test_legacy_succeeded_marker_is_not_backfilled_as_new_repair(self):
        self.seed()
        state = json.loads(self.path.read_text())
        state.pop('recovery_events')
        state['last_restart_result'] = 'succeeded'
        self.path.write_text(json.dumps(state))
        self.runtime.probes = [GOOD]
        self.assertEqual(self.tick(1060)['recovery_events'], [])

    def test_failed_pre_restart_state_write_prevents_restart(self):
        self.seed()
        with patch.object(health, 'save_state', side_effect=OSError('private fake error')):
            with self.assertRaises(OSError):
                self.tick(1060)
        self.assertEqual(self.runtime.restarted, 0)

    def test_corrupt_state_quarantines_recovery_for_one_hour(self):
        self.path.write_text('{broken private data')
        result = self.tick(1000)
        self.assertEqual(result['blocked_until'], 4600)
        self.tick(1060)
        self.assertEqual(self.tick(1120)['outcome'], 'state_cooldown')
        self.assertEqual(self.runtime.restarted, 0)
        self.assertNotIn('private', self.path.read_text())

    def test_clock_rollback_does_not_bypass_budget(self):
        self.seed(at=1500, attempts=[1400])
        self.assertEqual(self.tick(1000)['outcome'], 'clock_rollback')
        self.assertEqual(self.runtime.restarted, 0)

    def test_state_and_probe_diagnostics_do_not_keep_arbitrary_bodies(self):
        self.seed()
        raw = json.loads(self.path.read_text())
        raw['api_key'] = 'private-value'
        self.path.write_text(json.dumps(raw))
        self.runtime.probes = [{**GOOD, 'body': 'private-value'}]
        result = self.tick(1060)
        self.assertNotIn('private-value', json.dumps(result))
        self.assertNotIn('private-value', self.path.read_text())

    def test_inconsistent_probe_cannot_trigger_restart(self):
        self.seed()
        self.runtime.probes = [{**BAD, 'global_http_status': 500}]
        self.assertEqual(self.tick(1060)['consecutive_provider_503'], 0)
        self.assertEqual(self.runtime.restarted, 0)


class RuntimeChecks(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
                         'Root-only Unix export boundary')
    def test_root_export_is_atomic_group_readable_and_rejects_unsafe_targets(self):
        import grp
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'events'
            directory.mkdir(mode=0o750)
            os.chown(directory, 0, os.getgid())
            path = directory / 'events.json'
            state = {**health.initial_state(), **GOOD, 'checked_at': 1000,
                     'private_body': 'never-export'}
            with patch.object(health, 'EVENTS', path), \
                 patch.object(grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=os.getgid())):
                health.save_events(path, state)
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)
                self.assertNotIn('never-export', path.read_text())
                before = path.read_bytes()
                with patch.object(health.os, 'replace', side_effect=OSError('replace failed')):
                    with self.assertRaises(OSError):
                        health.save_events(path, state)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(directory.iterdir()), [path])
                path.unlink()
                private = Path(temp) / 'private'
                private.write_text('unchanged')
                path.symlink_to(private)
                with self.assertRaises(OSError):
                    health.save_events(path, state)
                self.assertEqual(private.read_text(), 'unchanged')
                path.unlink()
                directory.chmod(0o770)
                with self.assertRaises(OSError):
                    health.save_events(path, state)

    def test_global_health_and_provider_status_are_both_required(self):
        runtime = health.Runtime()
        with patch.object(runtime, 'http', side_effect=[(200, {'healthy': True}), (503, None)]):
            self.assertEqual(runtime.probe(), BAD)
        with patch.object(runtime, 'http', side_effect=[(200, {'healthy': True}), (200, {'all': []})]):
            self.assertEqual(runtime.probe(), GOOD)
        for status in (401, 403, 429, 500):
            with patch.object(runtime, 'http', side_effect=[(200, {'healthy': True}), (status, None)]):
                self.assertEqual(runtime.probe()['observation'], 'probe_unknown')
        with patch.object(runtime, 'http', return_value=(200, {'healthy': False})) as fetch:
            self.assertEqual(runtime.probe()['observation'], 'global_unhealthy')
            self.assertEqual(fetch.call_count, 1)

    def test_safe_requires_empty_successful_session_status(self):
        runtime = health.Runtime()
        with patch.object(runtime, 'unit_active', return_value=True), \
             patch.object(runtime, 'owner_idle', return_value=True), \
             patch.object(runtime, 'research_idle', return_value=True):
            for result, expected in [((200, {}), None), ((200, {'ses_x': {'type': 'busy'}}), 'sessions_busy'),
                                     ((503, None), 'sessions_unknown'), ((200, []), 'sessions_unknown')]:
                with patch.object(runtime, 'http', return_value=result):
                    self.assertEqual(runtime.safe(), expected)

    def test_finance_connection_is_readonly_and_only_checks_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'isolated.sqlite3'
            with closing(sqlite3.connect(database)) as db:
                db.execute('CREATE TABLE local_updates(state TEXT)')
                db.execute("INSERT INTO local_updates VALUES('done')")
                db.commit()
            before = database.read_bytes()
            with patch.object(health, 'DATABASE', database):
                self.assertTrue(health.Runtime().owner_idle())
            self.assertEqual(database.read_bytes(), before)
            with closing(sqlite3.connect(database)) as db:
                db.execute("UPDATE local_updates SET state='processing'")
                db.commit()
            with patch.object(health, 'DATABASE', database):
                self.assertFalse(health.Runtime().owner_idle())

    def test_atomic_save_preserves_old_state_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.json'
            health.save_state(path, {'old': 1})
            with patch.object(health.os, 'replace', side_effect=OSError('isolated failure')):
                with self.assertRaises(OSError):
                    health.save_state(path, {'new': 2})
            self.assertEqual(json.loads(path.read_text()), {'old': 1})
            self.assertEqual([p.name for p in path.parent.iterdir()], ['state.json'])

    @unittest.skipUnless(os.name == 'posix', 'Unix file locks and symlinks')
    def test_existing_shared_invocation_and_symlink_block_exclusive_recovery(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'inference.lock'
            path.touch()
            with path.open('r') as reader:
                fcntl.flock(reader.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    with health.exclusive(path):
                        self.fail('active invocation admitted')
            link = Path(temp) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                with health.exclusive(link):
                    self.fail('symlink admitted')


if __name__ == '__main__':
    unittest.main()
