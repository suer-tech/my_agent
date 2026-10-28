from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from iva_integration.recovery_schedules import collect


NOW = 1792930000.0
STAMP = datetime.fromtimestamp(NOW, timezone.utc).isoformat()


class RecoverySchedules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.finance = Path(self.tmp.name) / 'finance.sqlite3'
        self.research = Path(self.tmp.name) / 'research.sqlite3'
        self.f = sqlite3.connect(self.finance)
        self.r = sqlite3.connect(self.research)
        self.addCleanup(self.f.close)
        self.addCleanup(self.r.close)
        self.f.executescript('''
            CREATE TABLE scheduled_jobs(id TEXT PRIMARY KEY,spec TEXT,state TEXT,cursor TEXT,error TEXT);
            CREATE TABLE scheduled_runs(id TEXT PRIMARY KEY,job_id TEXT,state TEXT,finished TEXT,result TEXT);
            CREATE TABLE financial_sentinel(value TEXT);
            INSERT INTO financial_sentinel VALUES('untouched');
        ''')
        self.r.executescript('''
            CREATE TABLE demand_cycles(id TEXT PRIMARY KEY,state TEXT);
            CREATE TABLE demand_events(id INTEGER PRIMARY KEY AUTOINCREMENT,cycle_id TEXT,event TEXT,detail TEXT,created TEXT);
            CREATE TABLE demand_agent_tasks(id TEXT PRIMARY KEY,cycle_id TEXT,status TEXT);
        ''')
        self.r.execute("INSERT INTO demand_cycles VALUES('cycle-1','running')")
        self.r.execute("INSERT INTO demand_agent_tasks VALUES('task-1','cycle-1','done')")
        self.r.commit()
        self.state = None
        self.serial = 0

    def job(self, kind='demand_research', cursor=None):
        self.f.execute('INSERT INTO scheduled_jobs VALUES(?,?,?,?,?)',
                       ('job-1', json.dumps({'kind': kind}), 'active', json.dumps(cursor or {}), None))
        self.f.commit()

    def add_run(self, result, running=False):
        self.serial += 1
        identity = 'run-' + str(self.serial)
        self.f.execute('INSERT INTO scheduled_runs VALUES(?,?,?,?,?)',
                       (identity, 'job-1', 'running' if running else 'done', None if running else STAMP,
                        json.dumps(result)))
        self.f.execute('UPDATE scheduled_jobs SET error=? WHERE id=?',
                       ('secret full exception' if result.get('ok') is False else None, 'job-1'))
        if result.get('cursor'):
            self.f.execute('UPDATE scheduled_jobs SET cursor=? WHERE id=?', (json.dumps(result['cursor']), 'job-1'))
        self.f.commit()
        return identity

    def event(self, name, detail, cycle='cycle-1'):
        self.r.execute('INSERT INTO demand_events(cycle_id,event,detail,created) VALUES(?,?,?,?)',
                       (cycle, name, json.dumps(detail), STAMP))
        self.r.commit()

    def collect(self):
        result = collect(self.finance, self.research, self.state, NOW)
        self.state = result['state']
        return result['events']

    @staticmethod
    def final(**changes):
        return {'ok': True, 'has_more': False, 'cycle_id': 'cycle-1', 'stage': 'done',
                'status': 'completed', 'coverage': {}, **changes}

    def test_baseline_no_old_recovery_then_live_retry_once(self):
        self.job()
        self.add_run({'ok': False, 'error_category': 'model_timeout', 'cycle_id': 'cycle-1'})
        self.add_run(self.final())
        self.assertEqual(self.collect(), [])
        self.add_run({'ok': False, 'error_category': 'model_timeout', 'cycle_id': 'cycle-1'})
        self.assertEqual(self.collect(), [])
        self.add_run(self.final())
        events = self.collect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['code'], 'research_timeout')
        self.assertTrue(events[0]['proof']['terminal'])
        self.assertEqual(self.collect(), [])
        self.add_run(self.final())
        self.assertEqual(self.collect(), [])

    def test_current_failure_baselined_for_future_recovery_and_no_private_text(self):
        self.job()
        self.add_run({'ok': False, 'error_category': 'auth_unavailable', 'error': 'credential-secret'})
        self.assertEqual(self.collect(), [])
        self.add_run(self.final())
        events = self.collect()
        self.assertEqual(len(events), 1)
        self.assertNotIn('secret', json.dumps(events))
        self.assertNotIn('secret', json.dumps(self.state))

    def test_good_checkpoint_and_failed_terminal_are_not_recovery(self):
        self.job()
        self.collect()
        self.add_run({'ok': False, 'error_category': 'schema_invalid'})
        self.add_run({'ok': True, 'has_more': True, 'stage': 'agents'})
        self.add_run(self.final(coverage={'agent_progress': {'counts': {'failed': 1}}}))
        self.assertEqual(self.collect(), [])
        self.add_run(self.final(status='completed_with_gaps', coverage={'source_gaps': 3}))
        self.assertEqual(len(self.collect()), 1)

    def test_pending_run_is_not_lost_after_initial_baseline(self):
        self.job()
        identity = self.add_run({}, running=True)
        self.collect()
        self.f.execute('UPDATE scheduled_runs SET state=?,finished=?,result=? WHERE id=?',
                       ('failed', STAMP, json.dumps({'ok': False, 'error_category': 'model_timeout'}), identity))
        self.f.commit()
        self.collect()
        self.add_run(self.final())
        self.assertEqual(len(self.collect()), 1)

    def test_research_current_notice_survives_good_checkpoint_at_baseline(self):
        self.job()
        failed = self.add_run({'ok': False, 'error_category': 'model_timeout'})
        self.add_run({'ok': True, 'has_more': True})
        cursor = {'demand_notice_state': {'notices': {'timeout': {'run_id': failed}}}}
        self.f.execute('UPDATE scheduled_jobs SET error=?,cursor=?', ('retained failure', json.dumps(cursor)))
        self.f.commit()
        self.assertEqual(self.collect(), [])
        self.add_run(self.final())
        self.assertEqual(len(self.collect()), 1)

    def test_mail_requires_actual_read_not_empty_check(self):
        before = {'uidvalidity': '12', 'last_uid': 8}
        self.job('mail_watch', before)
        self.collect()
        self.add_run({'ok': False, 'error': 'body failed'})
        self.add_run({'ok': True, 'cursor': before, 'processed_uids': []})
        self.assertEqual(self.collect(), [])
        self.add_run({'ok': True, 'cursor': {'uidvalidity': '12', 'last_uid': 9}, 'read_only': True,
                  'processed_uids': [9], 'text': 'private mail body'})
        events = self.collect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['source'], 'mail')
        self.assertTrue(events[0]['proof']['cursor_advanced'])
        self.assertNotIn('private', json.dumps(events))

    def test_mail_baseline_or_validity_reset_cannot_later_claim_old_read_fixed(self):
        before = {'uidvalidity': '12', 'last_uid': 8}
        self.job('mail_watch', before)
        self.collect()
        for reset in ({'baseline': True, 'cursor': {'uidvalidity': '12', 'last_uid': 9}},
                      {'read_only': True, 'cursor': {'uidvalidity': '13', 'last_uid': 9}}):
            self.add_run({'ok': False})
            self.collect()
            self.add_run({'ok': True, **reset})
            self.assertEqual(self.collect(), [])
            self.add_run({'ok': True, 'read_only': True, 'cursor': {'uidvalidity': '12', 'last_uid': 10}})
            self.assertEqual(self.collect(), [])

    def quote(self, outcome, **changes):
        self.event('child_quote_repair', {'task_id': 'task-1', 'request_hash': 'abc', 'key': 'child-1',
                                          'outcome': outcome, **changes})

    def review(self, decision='approve'):
        self.event('review_completed', {'task_id': 'task-1', 'review': {'decisions': [
            {'key': 'child-1', 'decision': decision}]}})

    def test_quote_literal_repair_needs_later_coordinator_approval(self):
        self.collect()
        self.quote('started', category='agent_child_quote_not_found')
        self.quote('repaired', citation={'trigger_quote': 'private source text'})
        self.assertEqual(self.collect(), [])
        self.review()
        events = self.collect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['action'], 'quote_correction')
        self.assertFalse(events[0]['proof']['cycle_completed'])
        self.assertNotIn('private', json.dumps(events))
        self.assertNotIn('private', json.dumps(self.state))
        self.assertEqual(self.collect(), [])

    def test_quote_coalesces_same_cycle_category(self):
        self.collect()
        self.quote('started', category='agent_child_quote_not_found')
        self.quote('repaired')
        self.review()
        self.assertEqual(len(self.collect()), 1)
        self.quote('started', category='agent_child_quote_not_found', request_hash='def')
        self.quote('repaired', request_hash='def')
        self.review()
        self.assertEqual(self.collect(), [])

    def test_rejected_or_failed_parent_quote_is_not_fixed(self):
        self.collect()
        self.quote('started', category='agent_child_quote_not_found')
        self.quote('repaired')
        self.review('defer')
        self.assertEqual(self.collect(), [])
        self.quote('started', category='agent_child_quote_not_found', request_hash='def')
        self.quote('repaired', request_hash='def')
        self.r.execute("UPDATE demand_agent_tasks SET status='failed'")
        self.r.commit()
        self.review()
        self.assertEqual(self.collect(), [])

    def test_active_quote_baseline_then_real_approval_and_no_old_replay(self):
        self.quote('started', category='agent_child_quote_not_found')
        self.quote('repaired')
        self.collect()
        self.review()
        self.assertEqual(len(self.collect()), 1)
        self.state = None
        self.assertEqual(self.collect(), [])

    def test_plan_rejected_then_verified_not_just_checked_name(self):
        self.collect()
        self.event('scope_plan_rejected', {'limitations': [{'relationship': 'cross_lane'}]})
        self.event('scope_plan_checked', {'limitations': [], 'decisions': [{'relationship': 'cross_lane'}]})
        self.assertEqual(self.collect(), [])
        self.event('scope_plan_checked', {'limitations': [], 'decisions': [{'relationship': 'same_problem'}]})
        events = self.collect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['action'], 'plan_correction')

    def test_source_databases_never_modified_and_missing_source_does_not_reset(self):
        self.job()
        before_f, before_r = self.finance.read_bytes(), self.research.read_bytes()
        self.collect()
        self.assertEqual(self.finance.read_bytes(), before_f)
        self.assertEqual(self.research.read_bytes(), before_r)
        previous = json.loads(json.dumps(self.state))
        result = collect(Path(self.tmp.name) / 'missing', self.research, self.state, NOW)
        self.assertEqual(result['state']['schedules'], previous['schedules'])
        self.assertEqual(result['diagnostics'], {'schedules': 'source_unavailable', 'research': 'read_ok'})
        self.assertFalse((Path(self.tmp.name) / 'missing').exists())

    def test_mid_read_failure_does_not_advance_event_watermark(self):
        self.collect()
        self.quote('started', category='agent_child_quote_not_found')
        self.quote('repaired')
        self.review()
        prior = json.loads(json.dumps(self.state))
        self.r.execute('DROP TABLE demand_agent_tasks')
        self.r.commit()
        self.assertEqual(self.collect(), [])
        self.assertEqual(self.state['research'], prior['research'])
        self.r.execute('CREATE TABLE demand_agent_tasks(id TEXT PRIMARY KEY,cycle_id TEXT,status TEXT)')
        self.r.execute("INSERT INTO demand_agent_tasks VALUES('task-1','cycle-1','done')")
        self.r.commit()
        self.assertEqual(len(self.collect()), 1)

    def test_paused_task_never_generates_recovery(self):
        self.job()
        self.collect()
        self.add_run({'ok': False, 'error_category': 'model_timeout'})
        self.collect()
        self.f.execute("UPDATE scheduled_jobs SET state='paused'")
        self.f.commit()
        self.add_run(self.final())
        self.assertEqual(self.collect(), [])

    def test_newer_mail_failure_suppresses_stale_recovery_in_one_poll(self):
        before = {'uidvalidity': '12', 'last_uid': 8}
        self.job('mail_watch', before)
        self.collect()
        self.add_run({'ok': False})
        self.add_run({'ok': True, 'read_only': True, 'cursor': {'uidvalidity': '12', 'last_uid': 9}})
        self.add_run({'ok': False})
        self.assertEqual(self.collect(), [])
        self.add_run({'ok': True, 'read_only': True, 'cursor': {'uidvalidity': '12', 'last_uid': 10}})
        self.assertEqual(len(self.collect()), 1)

    def test_newer_pending_mail_run_defers_recovery_until_real_completion(self):
        self.job('mail_watch', {'uidvalidity': '12', 'last_uid': 8})
        self.collect()
        self.add_run({'ok': False})
        self.add_run({'ok': True, 'read_only': True, 'cursor': {'uidvalidity': '12', 'last_uid': 9}})
        pending = self.add_run({}, running=True)
        self.assertEqual(self.collect(), [])
        self.f.execute('UPDATE scheduled_runs SET state=?,finished=?,result=? WHERE id=?',
                       ('done', STAMP, json.dumps({'ok': True, 'read_only': True,
                        'cursor': {'uidvalidity': '12', 'last_uid': 10}}), pending))
        self.f.commit()
        self.assertEqual(len(self.collect()), 1)
        self.assertEqual(self.state['schedules']['pending'], [])

    def test_corrupt_checkpoint_is_silent_baseline(self):
        self.job()
        self.add_run({'ok': False, 'error_category': 'model_timeout'})
        self.add_run(self.final())
        self.state = {'version': 1, 'schedules': {'watermark': 'broken'}, 'research': []}
        self.assertEqual(self.collect(), [])
        self.assertIsInstance(self.state['schedules']['watermark'], int)
        self.assertIsInstance(self.state['research']['watermark'], int)
        self.add_run({'ok': False, 'error_category': 'model_timeout'})
        self.collect()
        self.add_run(self.final())
        self.assertEqual(len(self.collect()), 1)

    def test_source_rollback_same_rowid_new_identity_rebaselines(self):
        self.job()
        self.add_run({'ok': False, 'error_category': 'model_timeout'})
        self.collect()
        self.f.execute("UPDATE scheduled_runs SET id='restored-other-run'")
        self.f.commit()
        self.add_run(self.final())
        self.assertEqual(self.collect(), [])
        self.assertEqual(self.state['schedules']['episodes'], {})

    def test_research_source_rollback_is_not_confused_with_retention(self):
        self.event('unrelated', {})
        self.collect()
        self.r.execute("UPDATE demand_events SET cycle_id='other-cycle' WHERE id=1")
        self.r.commit()
        self.assertEqual(self.collect(), [])
        self.assertEqual(self.state['research']['quotes'], {})
        self.quote('started', category='agent_child_quote_not_found')
        self.collect()
        self.r.execute('DELETE FROM demand_events')
        self.r.commit()
        self.quote('repaired')
        self.review()
        self.assertEqual(len(self.collect()), 1)

    def test_quote_parent_and_event_are_read_from_one_snapshot(self):
        self.collect()
        self.quote('started', category='agent_child_quote_not_found')
        self.quote('repaired')
        self.r.execute("UPDATE demand_agent_tasks SET status='running'")
        self.r.commit()
        self.assertEqual(self.collect(), [])
        self.r.execute("UPDATE demand_agent_tasks SET status='done'")
        self.r.execute('INSERT INTO demand_events(cycle_id,event,detail,created) VALUES(?,?,?,?)',
                       ('cycle-1', 'review_completed', json.dumps({'task_id': 'task-1', 'review': {'decisions': [
                        {'key': 'child-1', 'decision': 'approve'}]}}), STAMP))
        self.r.commit()
        self.assertEqual(len(self.collect()), 1)


if __name__ == '__main__':
    unittest.main()
