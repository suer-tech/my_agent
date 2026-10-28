"""Deployment transaction tests use isolated files and fake systemd only."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import itertools
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import install_recovery_notices as installer


class RecoveryInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = self.root / 'data/recovery-notices-release-test'
        self.stage = self.release / 'source'
        self.stage.mkdir(parents=True)
        self.units = self.root / 'external/systemd'
        self.units.mkdir(parents=True)
        self.external = {str(self.root / 'external/opencode_health.py'): 'deploy/opencode_health.py',
                         **{str(self.units / name): 'deploy/systemd/' + name for name in
                            (installer.WATCHDOG_SERVICE, installer.SERVICE, installer.TIMER)}}
        for name, value in (('ROOT', self.root), ('RELEASE', self.release),
                            ('EVENTS', self.root / 'events'), ('NOTICES', self.root / 'data/notices'),
                            ('EXTERNAL', self.external)):
            self.mock(patch.object(installer, name, value))
        self.before = {}
        for target, rel in installer.mapping().items():
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            # The new notifier units and modules really were absent initially.
            if 'recovery_' not in rel and 'recovery-' not in rel:
                path.write_text('old source ' + rel)
                self.before[target] = path.read_bytes()
        for rel in installer.FILES:
            path = self.stage / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('reviewed source ' + rel)
        self.save_manifests()
        self.states = {
            ('is-active', installer.GATEWAY): 'active',
            ('is-active', installer.WATCHDOG_SERVICE): 'inactive',
            ('is-active', installer.SERVICE): 'inactive',
            ('is-active', installer.WATCHDOG_TIMER): 'active',
            ('is-enabled', installer.WATCHDOG_TIMER): 'enabled',
            ('is-active', installer.TIMER): 'unknown',
            ('is-enabled', installer.TIMER): 'not-found',
            ('is-active', installer.TELEGRAM): 'active',
            ('is-enabled', installer.TELEGRAM): 'enabled',
        }
        self.mock(patch.object(installer, 'unit_state', side_effect=lambda verb, unit: self.states[(verb, unit)]))
        self.mock(patch.object(installer, 'quiet', return_value=True))
        self.mock(patch.object(installer, 'protected_snapshot', return_value={'protected': 'same'}))
        self.preflight = self.mock(patch.object(installer, 'preflight'))
        self.private = self.mock(patch.object(installer, 'private_directory'))
        self.copies = []

        def replace(source, target, uid, gid, mode):
            self.copies.append((target, uid, gid, mode))
            shutil.copyfile(source, target)

        self.mock(patch.object(installer, 'replace', side_effect=replace))
        self.commands = []
        self.fail = None

        def run(args, **kwargs):
            self.commands.append(args)
            if self.fail is not None and self.fail(args):
                raise subprocess.CalledProcessError(1, args)
            if args[:2] == ['systemctl', 'stop']:
                self.states[('is-active', args[2])] = 'inactive'
            if args[:2] == ['systemctl', 'start']:
                self.states[('is-active', args[2])] = 'active'
            if args[:2] == ['systemctl', 'enable']:
                self.states[('is-enabled', args[2])] = 'enabled'
            if args[:2] == ['systemctl', 'disable']:
                self.states[('is-enabled', args[2])] = 'disabled'
            result = {'delivery': 'collect_only', 'collectors': {
                name: {'state': 'polled', 'diagnostics': {'schedules': 'read_ok', 'research': 'read_ok'}}
                for name in ('schedules', 'threads', 'opencode')}}
            return subprocess.CompletedProcess(args, 0, json.dumps(result), '')

        self.mock(patch.object(installer, 'run', side_effect=run))
        self.mock(patch.object(installer, 'restart_gateway',
                               side_effect=lambda: installer.run(['systemctl', 'restart', installer.GATEWAY])))
        self.mock(patch.object(installer, 'pause_telegram',
                               side_effect=lambda: installer.run(['systemctl', 'stop', installer.TELEGRAM])))
        self.account = SimpleNamespace(pw_uid=1000, pw_gid=1000)

    def mock(self, manager):
        result = manager.start()
        self.addCleanup(manager.stop)
        return result

    def save_manifests(self):
        (self.release / 'expected-base.json').write_text(json.dumps({
            target: installer.digest(Path(target)) for target in installer.mapping()}))
        (self.release / 'source-sha256.json').write_text(json.dumps({
            rel: installer.digest(self.stage / rel) for rel in installer.FILES}))

    def test_success_pauses_telegram_before_gateway_then_restores_without_touching_engine_opencode(self):
        report = installer.install(self.account)
        self.assertTrue(report['installed'])
        self.assertTrue(report['baseline_confirmed'])
        self.assertEqual(self.states[('is-active', installer.WATCHDOG_TIMER)], 'active')
        self.assertEqual(self.states[('is-enabled', installer.WATCHDOG_TIMER)], 'enabled')
        baseline = next(index for index, args in enumerate(self.commands) if '--collect-only' in args)
        activate = self.commands.index(['systemctl', 'start', installer.TIMER])
        self.assertLess(baseline, activate)
        pause = self.commands.index(['systemctl', 'stop', installer.TELEGRAM])
        reload = self.commands.index(['systemctl', 'restart', installer.GATEWAY])
        resume = self.commands.index(['systemctl', 'start', installer.TELEGRAM])
        self.assertLess(pause, reload)
        self.assertLess(reload, resume)
        self.assertEqual(self.states[('is-active', installer.TELEGRAM)], 'active')
        self.assertEqual(self.states[('is-enabled', installer.TELEGRAM)], 'enabled')
        self.assertTrue(report['telegram_briefly_paused'])
        for unit in ('iva@telegram', 'iva@engine', 'iva-opencode.service', installer.WATCHDOG_SERVICE, installer.SERVICE):
            self.assertFalse(any(unit in args for args in self.commands), unit)
        self.assertEqual([args for args in self.commands if args[:2] == ['systemctl', 'restart']],
                         [['systemctl', 'restart', installer.GATEWAY]])
        for target, rel in installer.mapping().items():
            self.assertEqual(Path(target).read_bytes(), (self.stage / rel).read_bytes())

    def test_preflight_failure_or_source_change_has_no_runtime_mutations(self):
        for mode in ('failed', 'source_changed', 'runtime_changed'):
            with self.subTest(mode=mode):
                self.save_manifests()
                self.commands.clear()

                def action():
                    if mode == 'failed':
                        raise RuntimeError('failed isolated tests')
                    if mode == 'source_changed':
                        (self.stage / installer.FILES[0]).write_text('concurrent edit')
                    else:
                        (self.root / 'deploy/opencode_health.py').write_text('runtime edit')

                self.preflight.side_effect = action
                with self.assertRaises(RuntimeError):
                    installer.install(self.account)
                self.assertEqual(self.commands, [])
                self.assertEqual(self.copies, [])

    def test_timer_race_is_not_interrupted_and_previous_timer_is_resumed(self):
        def action():
            # This is caught before stopping timer; no worker stop is issued.
            self.states[('is-active', installer.WATCHDOG_SERVICE)] = 'active'

        self.preflight.side_effect = action
        with self.assertRaisesRegex(RuntimeError, 'do not interrupt'):
            installer.install(self.account)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.copies, [])

    def test_failure_restores_files_and_exact_prior_timer_enable_active_state(self):
        self.fail = lambda args: '--collect-only' in args
        with self.assertRaises(subprocess.CalledProcessError):
            installer.install(self.account)
        for target in installer.mapping():
            if target in self.before:
                self.assertEqual(Path(target).read_bytes(), self.before[target])
            else:
                self.assertFalse(Path(target).exists(), target)
        self.assertEqual(self.states[('is-active', installer.WATCHDOG_TIMER)], 'active')
        self.assertEqual(self.states[('is-enabled', installer.WATCHDOG_TIMER)], 'enabled')
        self.assertFalse((self.release / 'installation.json').exists())
        self.assertEqual(self.states[('is-active', installer.TELEGRAM)], 'active')
        self.assertNotIn(['systemctl', 'start', installer.SERVICE], self.commands)
        self.assertNotIn(['systemctl', 'stop', installer.SERVICE], self.commands)

    def test_busy_owner_prevents_any_changes(self):
        with patch.object(installer, 'quiet', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'defer installation'):
                installer.install(self.account)
        self.preflight.assert_not_called()
        self.assertEqual(self.commands, [])

    def test_attempted_gateway_restart_with_failed_health_is_reloaded_on_rollback(self):
        with patch.object(installer, 'restart_gateway',
                          side_effect=[RuntimeError('health not confirmed'), None]) as reload:
            with self.assertRaisesRegex(RuntimeError, 'health not confirmed'):
                installer.install(self.account)
        self.assertEqual(reload.call_count, 2)
        for target in installer.mapping():
            if target in self.before:
                self.assertEqual(Path(target).read_bytes(), self.before[target])
            else:
                self.assertFalse(Path(target).exists(), target)
        self.assertEqual(self.states[('is-active', installer.WATCHDOG_TIMER)], 'active')

    def test_previously_inactive_telegram_is_not_started(self):
        self.states[('is-active', installer.TELEGRAM)] = 'inactive'
        report = installer.install(self.account)
        self.assertFalse(report['telegram_briefly_paused'])
        self.assertFalse(any(installer.TELEGRAM in args for args in self.commands))

    def test_incomplete_or_altered_manifest_is_rejected(self):
        reviewed = json.loads((self.release / 'source-sha256.json').read_text())
        reviewed.pop(installer.FILES[0])
        (self.release / 'source-sha256.json').write_text(json.dumps(reviewed))
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            installer.verify_sources()
        self.assertEqual(self.commands, [])


class GatewayAdmission(unittest.TestCase):
    @staticmethod
    def status(pid=100, invocation='a' * 32, job=''):
        return subprocess.CompletedProcess([], 0,
            f'MainPID={pid}\nInvocationID={invocation}\nActiveState=active\nSubState=running\nJob={job}\n', '')

    def test_reload_holds_writer_barrier_then_releases_without_data_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'data/iva-pilot').mkdir(parents=True)
            (root / 'data/iva-pilot/control.json').write_text('{"gateway_port": 12345}')
            database = root / 'data/local-finance.sqlite3'
            with closing(sqlite3.connect(database)) as db:
                db.execute('CREATE TABLE fixture(value TEXT)')
                db.execute("INSERT INTO fixture VALUES('kept')")
                db.commit()
            before = database.read_bytes()

            observations = iter([self.status(), self.status(), self.status(101, 'b' * 32)])

            def restart(args, **kwargs):
                self.assertLessEqual(kwargs['timeout'], 10)
                competing = sqlite3.connect(database, timeout=0)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        competing.execute('BEGIN IMMEDIATE')
                finally:
                    competing.close()
                if args[1] == 'show':
                    return next(observations)
                self.assertEqual(args, ['systemctl', '--no-block', 'restart', installer.GATEWAY])
                return subprocess.CompletedProcess(args, 0, '', '')

            response = SimpleNamespace(status=200, read=lambda count: b'{"ok":true}')
            from contextlib import nullcontext
            opener = SimpleNamespace(open=lambda *args, **kwargs: nullcontext(response))
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'require_quiet') as quiet, \
                 patch.object(installer, 'run', side_effect=restart), \
                 patch.object(installer.urllib.request, 'build_opener', return_value=opener):
                installer.restart_gateway()
            quiet.assert_called_once()
            self.assertEqual(database.read_bytes(), before)
            with closing(sqlite3.connect(database, timeout=0)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.rollback()

    def test_old_invocation_health_never_confirms_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'data/iva-pilot').mkdir(parents=True)
            (root / 'data/iva-pilot/control.json').write_text('{"gateway_port": 12345}')
            with closing(sqlite3.connect(root / 'data/local-finance.sqlite3')) as db:
                db.execute('CREATE TABLE fixture(value TEXT)')
            ticks = itertools.count()
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'require_quiet'), \
                 patch.object(installer, 'run', return_value=self.status()), \
                 patch.object(installer.time, 'monotonic', side_effect=lambda: next(ticks)), \
                 patch.object(installer.time, 'sleep'), \
                 patch.object(installer.urllib.request, 'build_opener') as opener:
                with self.assertRaisesRegex(RuntimeError, 'deadline|did not recover'):
                    installer.restart_gateway()
            opener.return_value.open.assert_not_called()

    def test_busy_database_admission_never_restarts_gateway(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'data/iva-pilot').mkdir(parents=True)
            (root / 'data/iva-pilot/control.json').write_text('{"gateway_port": 12345}')
            database = root / 'data/local-finance.sqlite3'
            with closing(sqlite3.connect(database)) as owner:
                owner.execute('CREATE TABLE fixture(value TEXT)')
                owner.execute('BEGIN IMMEDIATE')
                with patch.object(installer, 'ROOT', root), patch.object(installer, 'run') as run:
                    with self.assertRaises(sqlite3.OperationalError):
                        installer.restart_gateway()
                    run.assert_not_called()

    def test_graceful_telegram_stop_releases_database_before_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'data').mkdir()
            database = root / 'data/local-finance.sqlite3'
            with closing(sqlite3.connect(database)) as db:
                db.execute('CREATE TABLE fixture(value TEXT)')

            def queue(args, **kwargs):
                self.assertEqual(args, ['systemctl', '--no-block', 'stop', installer.TELEGRAM])
                self.assertEqual(kwargs['timeout'], 2)
                with closing(sqlite3.connect(database, timeout=0)) as other:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute('BEGIN IMMEDIATE')

            states = iter(['deactivating', 'inactive'])

            def observed(*args):
                # Waiting on the long poll never holds the financial write lock.
                with closing(sqlite3.connect(database, timeout=0)) as other:
                    other.execute('BEGIN IMMEDIATE')
                    other.rollback()
                return next(states)

            with patch.object(installer, 'ROOT', root), patch.object(installer, 'require_quiet'), \
                 patch.object(installer, 'run', side_effect=queue) as run, \
                 patch.object(installer, 'unit_state', side_effect=observed), \
                 patch.object(installer.time, 'sleep'):
                installer.pause_telegram()
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
