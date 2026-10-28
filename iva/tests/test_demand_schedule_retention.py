"""Retention touches diagnostic JSON only; every database here is isolated."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from iva_integration import demand_schedule_retention as retention


NOW = datetime(2026, 10, 25, 15, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=14)
OLD = (CUTOFF - timedelta(days=1)).isoformat()
RECENT = NOW.isoformat()


class ScheduleRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'isolated.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.db.executescript('''
            CREATE TABLE scheduled_jobs(id TEXT PRIMARY KEY,spec TEXT,state TEXT,cursor TEXT);
            CREATE TABLE scheduled_runs(id TEXT PRIMARY KEY,job_id TEXT,slot TEXT,state TEXT,
                started TEXT,finished TEXT,result TEXT,error TEXT);
            CREATE TABLE scheduled_outbox(id TEXT PRIMARY KEY,job_id TEXT,run_id TEXT,
                text TEXT,state TEXT,message_id INTEGER,error TEXT);
            CREATE TABLE operations(id INTEGER PRIMARY KEY,amount TEXT,description TEXT);
            INSERT INTO operations VALUES(1,'123.45','financial state must not change');
        ''')
        self.job()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def job(self, identity='research', *, kind='demand_research', state='active', cursor=None):
        raw = cursor if isinstance(cursor, str) else json.dumps(cursor or {})
        self.db.execute('INSERT INTO scheduled_jobs VALUES(?,?,?,?)',
                        (identity, json.dumps({'kind': kind}), state, raw))

    def payload(self, **extra):
        return {'ok': True, 'has_more': True, 'status': 'analyze', 'cycle_id': 'historical',
                'coverage': {'pages': 'Большая история ' * 500},
                'cursor': {'old_checkpoint': 'private-cursor-value'}, **extra}

    def add_run(self, identity, *, job='research', state='continued', finished=OLD, result=None):
        raw = result if isinstance(result, str) else json.dumps(
            self.payload() if result is None else result, ensure_ascii=False)
        self.db.execute('INSERT INTO scheduled_runs VALUES(?,?,?,?,?,?,?,?)',
                        (identity, job, identity, state, OLD, finished, raw, 'original column error'))
        return raw

    def recent(self, job='research', *, finals=True):
        for index in range(5):
            self.add_run(f'{job}-recent-{index}', job=job, state='done' if finals else 'continued',
                     finished=RECENT, result=self.payload(has_more=not finals))
        self.db.commit()

    def raw(self, identity):
        return self.db.execute('SELECT result FROM scheduled_runs WHERE id=?', (identity,)).fetchone()[0]

    def call(self, name='apply', **kwargs):
        return getattr(retention, name)(self.db, CUTOFF, now=NOW, **kwargs)

    def table_digest(self, name):
        # Table names are hardcoded fixtures, never untrusted identifiers.
        rows = self.db.execute(f'SELECT * FROM {name} ORDER BY rowid').fetchall()
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()

    def test_dry_run_then_summary_preserves_semantics_and_digest(self):
        value = self.payload(stage='analyze', error='Ошибка источника', error_category='source_gap',
                             text='Краткий результат', summary={'evidence': 3},
                             contexts=['context'], handoff={'bulky': 'x' * 1000})
        before = self.add_run('candidate', result=value)
        self.recent()
        other_columns = self.db.execute('SELECT id,job_id,state,finished,error FROM scheduled_runs WHERE id=?',
                                        ('candidate',)).fetchone()
        changes = self.db.total_changes
        report = self.call('plan')
        self.assertEqual(report['candidate_run_ids'], ['candidate'])
        self.assertGreater(report['estimated_savings_bytes'], 0)
        self.assertEqual(self.raw('candidate'), before)
        self.assertEqual(self.db.total_changes, changes)
        self.assertFalse(self.db.in_transaction)
        self.assertNotIn('private-cursor-value', json.dumps(report))
        applied = self.call()
        self.assertEqual(applied['updated_rows'], 1)
        self.assertEqual(applied['reclaimed_logical_bytes'], report['estimated_savings_bytes'])
        compact = json.loads(self.raw('candidate'))
        self.assertEqual({key: compact[key] for key in retention.PRESERVE if key in value},
                         {key: value[key] for key in retention.PRESERVE if key in value})
        self.assertEqual(set(compact), set(retention.PRESERVE) | {'storage'})
        self.assertEqual(compact['storage']['original_bytes'], len(before.encode('utf-8')))
        self.assertEqual(compact['storage']['sha256'], hashlib.sha256(before.encode('utf-8')).hexdigest())
        self.assertEqual(compact['storage']['compacted_at'], NOW.isoformat(timespec='seconds'))
        self.assertEqual(other_columns, self.db.execute(
            'SELECT id,job_id,state,finished,error FROM scheduled_runs WHERE id=?', ('candidate',)).fetchone())

    def test_idempotence_and_no_pointless_growth(self):
        self.add_run('candidate')
        self.add_run('tiny', result={'ok': True})
        self.recent()
        self.assertEqual(self.call()['updated_rows'], 1)
        first = self.raw('candidate')
        report = self.call()
        self.assertEqual(report['updated_rows'], 0)
        self.assertEqual(report['skipped']['already_compacted'], 1)
        self.assertEqual(report['skipped']['no_savings'], 1)
        self.assertEqual(first, self.raw('candidate'))

    def test_current_recursive_pending_result_incident_and_explicit_cycle_guards(self):
        cursor = {'cycle_id': 'active', 'pending_result': {'nested': [{'demand_cycle_id': 'pending'}]},
                  'demand_notice_state': {'notices': {'auth': {'run_id': 'incident-run', 'cycle_id': 'incident'}}},
                  'token': 'never-print-me'}
        self.db.execute('UPDATE scheduled_jobs SET cursor=?', (json.dumps(cursor),))
        for cycle in ['active', 'pending', 'incident', 'external']:
            self.add_run(cycle, result=self.payload(cycle_id=cycle))
        self.add_run('incident-run')
        self.add_run('nested-active', result=self.payload(handoff={'pending_result': {'cycle_id': 'active'}}))
        self.add_run('candidate')
        self.recent()
        protected = {'external'}
        report = self.call(protected_cycle_ids=protected)
        self.assertEqual(report['candidate_run_ids'], ['candidate'])
        self.assertEqual(set(report['protected_cycle_ids']), {'active', 'pending', 'incident', 'external'})
        self.assertEqual(protected, {'external'})
        self.assertNotIn('never-print-me', json.dumps(report))
        self.assertEqual(self.db.execute('SELECT cursor FROM scheduled_jobs').fetchone()[0], json.dumps(cursor))

    def test_unacknowledged_outbox_protected_but_sent_and_cancelled_can_compact(self):
        for state in ['pending', 'sending', 'uncertain', 'sent', 'cancelled']:
            self.add_run(state)
            self.db.execute('INSERT INTO scheduled_outbox VALUES(?,?,?,?,?,?,?)',
                            (state, 'research', state, 'notification', state, None, None))
        self.recent()
        digest = self.table_digest('scheduled_outbox')
        report = self.call()
        self.assertEqual(set(report['candidate_run_ids']), {'sent', 'cancelled'})
        self.assertEqual(digest, self.table_digest('scheduled_outbox'))

    def test_running_and_invalid_cursor_jobs_block_entire_job(self):
        for job, state, cursor in [('busy', 'running', {}), ('bad', 'active', 'not json'),
                                    ('bad-reference', 'active', {'pending_result': {'cycle_id': []}})]:
            self.job(job, state=state, cursor=cursor)
            self.add_run(job + '-old', job=job)
            self.recent(job)
        report = self.call()
        self.assertEqual(set(report['protected_job_ids']), {'busy', 'bad', 'bad-reference'})
        self.assertEqual(report['updated_rows'], 0)

    def test_latest_five_runs_and_latest_five_final_outcomes_are_preserved(self):
        for i in range(7):
            self.add_run('final-' + str(i), state='done', result=self.payload(has_more=False))
        for i in range(6):
            self.add_run('checkpoint-' + str(i))
        self.db.commit()
        report = self.call()
        self.assertEqual(report['candidate_run_ids'], ['final-0', 'final-1', 'checkpoint-0'])

    def test_malformed_legacy_results_are_preserved_without_guessing(self):
        fixtures = [None, [], 'broken{', '{"ok":NaN}', '{"ok":true,"ok":false}',
                    self.payload(ok='true'), self.payload(has_more=1), self.payload(cycle_id=[]),
                    self.payload(storage={'format': 'someone-else'}),
                    self.payload(contexts=[{'active_cycle_id': 12}])]
        for i, value in enumerate(fixtures):
            raw = value if isinstance(value, str) else json.dumps(value)
            self.add_run(str(i), result=raw)
        self.recent()
        before = self.table_digest('scheduled_runs')
        report = self.call()
        self.assertEqual(report['updated_rows'], 0)
        self.assertEqual(before, self.table_digest('scheduled_runs'))
        self.assertEqual(report['skipped']['result_invalid'], 5)

    def test_only_research_result_column_changes_and_finances_are_untouched(self):
        self.job('mail', kind='mail_watch')
        self.job('agent', kind='agent')
        self.add_run('candidate')
        mail = self.add_run('mail-old', job='mail')
        agent = self.add_run('agent-old', job='agent')
        self.recent()
        before = {name: self.table_digest(name) for name in ['operations', 'scheduled_jobs', 'scheduled_outbox']}
        self.assertEqual(self.call()['updated_rows'], 1)
        self.assertEqual(before, {name: self.table_digest(name) for name in before})
        self.assertEqual(mail, self.raw('mail-old'))
        self.assertEqual(agent, self.raw('agent-old'))
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_bounded_batches_continue_without_rewriting_first_batch(self):
        for i in range(5):
            self.add_run('old-' + str(i))
        self.recent()
        first = self.call(batch_size=2)
        self.assertEqual(first['candidate_run_ids'], ['old-0', 'old-1'])
        self.assertTrue(first['more_eligible'])
        first_raw = self.raw('old-0')
        second = self.call(batch_size=2)
        self.assertEqual(second['candidate_run_ids'], ['old-2', 'old-3'])
        self.assertTrue(second['more_eligible'])
        final = self.call(batch_size=2)
        self.assertEqual(final['candidate_run_ids'], ['old-4'])
        self.assertFalse(final['more_eligible'])
        self.assertEqual(first_raw, self.raw('old-0'))

    def test_failed_update_rolls_back_entire_batch(self):
        self.add_run('first')
        self.add_run('second')
        self.recent()
        before = self.table_digest('scheduled_runs')
        self.db.executescript("""CREATE TRIGGER abort_second BEFORE UPDATE ON scheduled_runs
            WHEN OLD.id='second' BEGIN SELECT RAISE(ABORT,'fixture failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call()
        self.assertEqual(before, self.table_digest('scheduled_runs'))
        self.assertFalse(self.db.in_transaction)

    def test_secure_delete_removes_discarded_payload_from_database_pages(self):
        marker = 'obsolete-research-payload-7fe8d9b21d'
        # Build a clean fixture: OFF during earlier B-tree splits can already
        # leave obsolete copies, unrelated to this compaction's UPDATE.
        self.db.execute('PRAGMA secure_delete=ON')
        self.add_run('candidate', result=self.payload(coverage=marker * 1000))
        self.recent()
        self.assertTrue(marker.encode() in self.path.read_bytes(), 'fixture marker absent')
        self.db.execute('PRAGMA secure_delete=OFF')
        self.assertEqual(self.call()['updated_rows'], 1)
        self.assertFalse(marker.encode() in self.path.read_bytes(), 'compacted payload remains in database pages')
        self.assertEqual(self.db.execute('PRAGMA secure_delete').fetchone()[0], 0)

    def test_apply_rechecks_protection_after_earlier_plan(self):
        self.add_run('candidate')
        self.recent()
        self.assertEqual(self.call('plan')['candidate_rows'], 1)
        self.db.execute('UPDATE scheduled_jobs SET cursor=?', (json.dumps({'cycle_id': 'historical'}),))
        self.db.commit()
        self.assertEqual(self.call()['updated_rows'], 0)

    def test_plan_supports_read_only_connection_and_does_not_commit_caller(self):
        self.add_run('candidate')
        self.recent()
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as readonly:
            readonly.row_factory = sqlite3.Row
            report = retention.plan(readonly, CUTOFF, now=NOW)
            self.assertEqual(report['candidate_rows'], 1)
            self.assertFalse(readonly.in_transaction)
        self.db.execute("UPDATE operations SET amount='999'")
        self.assertEqual(self.call('plan')['candidate_rows'], 1)
        self.assertTrue(self.db.in_transaction)
        with self.assertRaisesRegex(ValueError, 'own_transaction'):
            self.call()
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT amount FROM operations').fetchone()[0], '123.45')

    def test_date_and_unfinished_boundaries(self):
        self.add_run('before', finished=(CUTOFF - timedelta(seconds=1)).astimezone(timezone(timedelta(hours=5))).isoformat())
        self.add_run('exact', finished=CUTOFF.isoformat())
        self.add_run('after', finished=(CUTOFF + timedelta(seconds=1)).isoformat())
        self.add_run('unfinished', finished=None)
        self.add_run('running', state='running')
        self.add_run('pending', state='pending')
        self.add_run('naive', finished='2026-09-01T00:00:00')
        self.add_run('invalid', finished='not a date')
        self.recent()
        self.assertEqual(self.call()['candidate_run_ids'], ['before'])

    def test_invalid_arguments_fail_before_mutation(self):
        self.recent()
        for batch in [0, -1, 501, True, 2.5]:
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.call(batch_size=batch)
        for cutoff in [datetime(2026, 1, 1), NOW + timedelta(seconds=1)]:
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                retention.apply(self.db, cutoff, now=NOW)
        for cycles in ['cycle', [''], [123]]:
            with self.subTest(cycles=cycles), self.assertRaises(ValueError):
                self.call(protected_cycle_ids=cycles)
        self.assertFalse(self.db.in_transaction)


if __name__ == '__main__':
    unittest.main()
