import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from iva_integration import recovery_notices as r


NOW = 2_000_000.0


def event(kind='threads', ident='test:one', stamp=NOW - 10):
    value = {'id': ident, 'verified_at': stamp}
    if kind == 'threads':
        value.update(source='threads', code='messages', action='code_patch', campaign='extension',
            proof={'job_id': 'a' * 32, 'installed': True, 'tests_passed': True,
                   'installed_at': stamp - 20, 'inbox_checked_at': stamp - 10,
                   'inbox_finished_at': stamp - 5, 'all_folders_checked': True,
                   'latest_incident_job': True, 'incident_inactive': True})
    elif kind == 'mail':
        value.update(source='mail', code='mail_read_failed', action='retry',
            proof={'job_id': 'job1', 'failed_run_id': 'run1', 'verified_run_id': 'run2',
                   'read_only': True, 'cursor_advanced': True, 'uidvalidity_unchanged': True})
    elif kind == 'quote':
        value.update(source='research', code='agent_child_sources_not_read', action='quote_correction',
            proof={'cycle_id': 'cycle1', 'task_id': 'task1', 'failed_event_id': 10,
                   'repair_event_id': 11, 'review_event_id': 12, 'literal_quote_verified': True,
                   'coordinator_approved': True, 'parent_task_completed': True, 'cycle_completed': False})
    elif kind == 'plan':
        value.update(source='research', code='research_scope_plan_rejected', action='plan_correction',
            proof={'cycle_id': 'cycle1', 'failed_event_id': 10, 'verified_event_id': 11,
                   'independent_scope_check': True, 'cycle_completed': False})
    elif kind == 'research':
        value.update(source='research', code='research_timeout', action='retry',
            proof={'job_id': 'job1', 'failed_run_id': 'run1', 'verified_run_id': 'run2',
                   'cycle_id': 'cycle2', 'failed_cycle_id': 'cycle1', 'same_cycle': False,
                   'terminal': True, 'technical_failures': 0})
    elif kind == 'opencode':
        value.update(source='opencode', code='provider_unavailable', action='service_restart',
            proof={'attempted_at': stamp - 10, 'restart_succeeded': True, 'global_healthy': True,
                   'provider_available': True, 'provider_503_confirmed': True})
    else:
        raise ValueError(kind)
    return value


class RecoveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = r.Store(self.root)
        self.addCleanup(lambda: self.store.close())

    def commit(self, events=None, state=None):
        self.store.commit_collection('test', {'state': state or {'version': 1},
                                             'events': [event()] if events is None else events}, NOW)

    def state(self, ident='test:one'):
        row = self.store.db.execute('SELECT state FROM notices WHERE id=?', (ident,)).fetchone()
        return row[0] if row else None

    def test_cursor_and_outbox_commit_together_and_duplicate_ids_never_requeue(self):
        self.commit()
        self.assertEqual(self.store.cursor('test'), {'version': 1})
        self.assertEqual(self.store.deliver(lambda: True, lambda _: {'message_id': 7}, NOW), 'sent')
        self.commit(state={'version': 1, 'next': 2})
        self.assertEqual(self.state(), 'sent')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notices').fetchone()[0], 1)
        self.assertEqual(self.store.cursor('test')['next'], 2)

    def test_invalid_event_does_not_advance_cursor_or_partially_insert(self):
        invalid = event('threads', 'test:bad')
        invalid['proof'] = {}
        with self.assertRaises(ValueError):
            self.commit([event(), invalid])
        self.assertIsNone(self.store.cursor('test'))
        self.assertIsNone(self.state())

    def test_sql_failure_rolls_back_earlier_event_and_cursor(self):
        self.store.db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON notices WHEN new.id='test:bad' BEGIN SELECT RAISE(ABORT,'isolated test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.commit([event(), event('mail', 'test:bad')])
        self.assertIsNone(self.store.cursor('test'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notices').fetchone()[0], 0)

    def test_preflight_failure_never_claims_or_sends(self):
        self.commit()
        send = Mock()
        for ready in (lambda: False, Mock(side_effect=RuntimeError('SECRET'))):
            self.assertEqual(self.store.deliver(ready, send, NOW), 'preflight_failed')
            self.assertEqual(self.state(), 'pending')
        send.assert_not_called()

    def test_send_is_claimed_before_external_action_and_ack_required(self):
        self.commit()
        def send(_):
            self.assertEqual(self.state(), 'sending')
            return {'message_id': 123}
        self.assertEqual(self.store.deliver(lambda: True, send, NOW), 'sent')
        row = self.store.db.execute('SELECT message_id,attempted_at,delivered_at FROM notices').fetchone()
        self.assertEqual(tuple(row), (123, NOW, NOW))
        self.assertEqual(self.store.deliver(lambda: True, Mock(), NOW), 'idle')

    def test_ambiguous_delivery_is_durable_and_never_retried(self):
        self.commit()
        send = Mock(side_effect=TimeoutError('SECRET telegram token'))
        self.assertEqual(self.store.deliver(lambda: True, send, NOW), 'uncertain')
        self.commit()
        self.assertEqual(self.store.deliver(lambda: True, send, NOW), 'idle')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.state(), 'uncertain')

    def test_missing_false_and_string_ack_are_uncertain(self):
        for index, result in enumerate((None, {}, {'message_id': False}, {'message_id': '1'}, {'message_id': 0})):
            ident = 'ack:' + str(index)
            self.commit([event('mail', ident)])
            self.assertEqual(self.store.deliver(lambda: True, lambda _, result=result: result, NOW), 'uncertain')
            self.assertEqual(self.state(ident), 'uncertain')

    def test_crash_during_send_is_not_retried_after_restart(self):
        self.commit()
        with self.assertRaises(SystemExit):
            self.store.deliver(lambda: True, Mock(side_effect=SystemExit()), NOW)
        self.assertEqual(self.state(), 'sending')
        self.store.close()
        self.store = r.Store(self.root)
        self.assertEqual(self.state(), 'uncertain')
        send = Mock()
        self.assertEqual(self.store.deliver(lambda: True, send, NOW), 'idle')
        send.assert_not_called()

    def test_expiration_prevents_late_backlog_and_retention_preserves_dedup(self):
        self.commit([event('mail', 'old:event', NOW - r.MAX_AGE - 1)])
        self.assertEqual(self.state('old:event'), 'expired')
        self.commit()
        self.store.maintain(NOW + r.MAX_AGE + 1)
        self.assertEqual(self.state(), 'expired')
        self.store.maintain(NOW + r.RETENTION + 1)
        self.assertEqual(tuple(self.store.db.execute("SELECT proof,text FROM notices WHERE id='test:one'").fetchone()), ('{}', ''))
        self.commit()
        self.assertEqual(self.state(), 'expired')

    def test_setup_notice_is_idempotent(self):
        self.store.setup_notice(NOW)
        self.store.setup_notice(NOW + 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notices').fetchone()[0], 1)
        self.assertIn('подтверждённых исправлениях', self.store.db.execute('SELECT text FROM notices').fetchone()[0])

    def test_collector_failure_isolated_and_private_errors_not_exposed(self):
        self.commit(state={'version': 1, 'saved': True})
        def failed(previous, now):
            self.assertTrue(previous['saved'])
            raise RuntimeError('SECRET password and error details')
        result = r.collect_all(self.store, {'test': failed,
                    'healthy': lambda previous, now: {'state': {'v': 1}, 'events': []}}, NOW)
        self.assertEqual(result['test'], {'state': 'failed', 'error_type': 'RuntimeError'})
        self.assertEqual(result['healthy']['state'], 'polled')
        self.assertTrue(self.store.cursor('test')['saved'])
        self.assertEqual(self.store.cursor('healthy'), {'v': 1})
        self.assertNotIn('SECRET', json.dumps(result))

    def test_collector_diagnostics_allow_only_fixed_source_status_enums(self):
        good = lambda previous, now: {'state': {}, 'events': [],
                                     'diagnostics': {'research': 'source_unavailable', 'schedules': 'read_ok'}}
        value = r.collect_all(self.store, {'schedules': good}, NOW)
        self.assertEqual(value['schedules']['state'], 'polled')
        self.assertEqual(value['schedules']['diagnostics']['research'], 'source_unavailable')
        bad = lambda previous, now: {'state': {}, 'events': [], 'diagnostics': {'research': 'SECRET'}}
        value = r.collect_all(self.store, {'bad': bad}, NOW)
        self.assertEqual(value['bad'], {'state': 'failed', 'error_type': 'ValueError'})
        self.assertIsNone(self.store.cursor('bad'))
        self.assertNotIn('SECRET', json.dumps(value))

    def test_status_has_no_raw_proof_or_source_data(self):
        self.commit()
        value = self.store.status(NOW, {}, 'sent')
        self.assertNotIn('proof', value['recent'][0])
        self.assertNotIn('job_id', json.dumps(value))
        self.assertEqual(value['policy'], 'confirmed_recovery_only_no_uncertain_retries')
        r.save_status(self.root, value)
        self.assertFalse(r.read_status(self.root, NOW)['stale'])
        self.assertTrue(r.read_status(self.root, NOW + 181)['stale'])

    def test_missing_oversized_and_malformed_status_fail_closed(self):
        self.assertEqual(r.read_status(self.root, NOW), {'available': False, 'stale': True})
        path = self.root / 'status.json'
        for value in ([], [1], None, 1, 'text', {}, {'version': 1, 'checked_at': True},
                      {'version': 1, 'checked_at': float('nan')}, {'version': 2, 'checked_at': NOW}):
            with self.subTest(value=value):
                path.write_text(json.dumps(value), encoding='utf-8')
                self.assertEqual(r.read_status(self.root, NOW), {'available': False, 'stale': True})
        path.write_text('x' * (r.MAX_JSON + 1), encoding='utf-8')
        self.assertEqual(r.read_status(self.root, NOW), {'available': False, 'stale': True})


class RecoveryMessageTests(unittest.TestCase):
    def test_all_supported_event_templates_have_proof_and_local_time(self):
        for kind in ('threads','mail','quote','plan','research','opencode'):
            with self.subTest(kind=kind):
                text = r.validate(event(kind), NOW)
                self.assertIn('Подтверждено:', text)
                self.assertIn('(Екатеринбург)', text)
                self.assertLess(len(text), 700)

    def test_external_fields_are_never_interpolated_and_extra_proof_rejected(self):
        value = event()
        value.update(root_cause='SECRET', error='SECRET', url='https://private.invalid/SECRET')
        self.assertNotIn('SECRET', r.validate(value, NOW))
        value['proof']['raw_error'] = 'SECRET'
        with self.assertRaises(ValueError):
            r.validate(value, NOW)

    def test_empty_proof_false_or_missing_required_flags_do_not_claim_recovery(self):
        for kind in ('threads','mail','quote','plan','research','opencode'):
            value = event(kind)
            invalid = copy.deepcopy(value)
            invalid['proof'] = {}
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                r.validate(invalid, NOW)
            for key, item in value['proof'].items():
                if item is True:
                    invalid = copy.deepcopy(value)
                    invalid['proof'][key] = 1
                    with self.subTest(kind=kind, flag=key), self.assertRaises(ValueError):
                        r.validate(invalid, NOW)

    def test_invalid_identity_time_code_or_nonfinite_proof_rejected(self):
        for changes in ({'id': 123}, {'id': 'secret\ntext'}, {'verified_at': True},
                        {'verified_at': NOW + 1}, {'verified_at': float('nan')}, {'code': 'unknown'}):
            value = event()
            value.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                r.validate(value, NOW)
        value = event()
        value['proof']['inbox_finished_at'] = NOW + 1
        with self.assertRaises(ValueError):
            r.validate(value, NOW)

    def test_quote_and_plan_notices_do_not_claim_whole_cycle_completed(self):
        for code in ('agent_child_quote_not_found','agent_child_quote_too_long','agent_child_sources_not_read'):
            value = event('quote')
            value['code'] = code
            text = r.validate(value, NOW)
            self.assertIn('Это исправление отдельного этапа.', text)
            self.assertIn('с прочитанным источником', text)
            self.assertNotIn('исследование завершено', text.lower())
        self.assertIn('этапа планирования', r.validate(event('plan'), NOW))

    def test_research_next_cycle_does_not_claim_old_report_fixed(self):
        value = event('research')
        self.assertIn('не исправление старого отчёта', r.validate(value, NOW))
        value['proof']['same_cycle'] = True
        with self.assertRaises(ValueError):
            r.validate(value, NOW)
        value['proof']['failed_cycle_id'] = value['proof']['cycle_id']
        self.assertIn('этого цикла', r.validate(value, NOW))

    def test_child_repair_requires_later_independent_review(self):
        value = event('quote')
        value['proof']['review_event_id'] = value['proof']['repair_event_id']
        with self.assertRaises(ValueError):
            r.validate(value, NOW)

    def test_opencode_does_not_claim_model_inference_completed(self):
        text = r.validate(event('opencode'), NOW)
        self.assertIn('доступа к провайдеру', text)
        self.assertNotIn('ответ модели', text)


if __name__ == '__main__':
    unittest.main()
