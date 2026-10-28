import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import demand_agents as agents
from iva_integration import demand_research as research
from iva_integration import demand_retention as retention


OLD = '2026-09-01T12:00:00+00:00'
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
BODY = 'Public source body. A buyer still reconciles orders manually. ' * 100


class DemandRetention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = research.Research(Path(self.temp.name) / 'research.sqlite3', Path(self.temp.name) / 'cards')
        self.addCleanup(self.store.close)

    def cycle(self, identity='old', *, state='completed', stage='done', date=OLD, version=2):
        plan = {'version': version, 'breadth_goal': 1, 'max_urls': 48,
                'baseline': {'old-hypothesis': {'state': 'signal', 'signature': 'x' * 10000}},
                'support_gate': 'Observe a real buyer problem', 'prior_context': ['old context' * 100]}
        result = {'ok': True, 'status': state, 'cycle_id': identity, 'has_more': False,
                  'text': 'Historical result, not proof of willingness to pay', 'coverage': {}}
        with self.store.db:
            self.store.db.execute('''INSERT INTO demand_cycles
                (id,job_id,slot,sequence,track,topic,task,stage,state,created,updated,finished,plan,result,discovery)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identity, 'job', identity, 0, 'software', 'Inventory mismatch', 'Investigate demand', stage,
                 state, date, date, date, json.dumps(plan), json.dumps(result),
                 json.dumps({'queries': ['inventory mismatch'], 'web_events': ['x' * 12000]})))
            self.store.db.execute('''INSERT INTO demand_batches(cycle_id,batch_index,focus,state,discovery)
                VALUES(?,0,'Counter-search','done',?)''', (identity, json.dumps({'queries': ['alternatives'], 'web_events': ['x' * 12000]})))
            self.store.event(identity, 'discovery', 'completed', {'web_events': ['x' * 15000]})
        return identity

    def source(self, cycle='old', identity='source', body=BODY, date=OLD):
        url = 'https://example.test/' + identity
        with self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO demand_sources VALUES(?,?,?,?,?,?,?)',
                                  (identity, url, 'Public source', body, research.digest(research.norm(body)), date, None))
            self.store.db.execute('''INSERT INTO demand_candidates
                (cycle_id,url,purpose,source_type,state,source_id,analysis_state)
                VALUES(?,?,'support','request','read',?,'done')''', (cycle, url, identity))
        return identity

    def rows(self, sql, args=()):
        return self.store.rows(sql, args)

    def dump(self):
        return list(self.store.db.iterdump())

    def apply(self, **kwargs):
        return retention.compact(self.store, clock=NOW, dry_run=False, **kwargs)

    def test_dry_run_is_read_only_apply_matches_estimate_and_history_survives(self):
        self.cycle(); self.source()
        observation = {'id': 'obs_1', 'task_id': 'researcher', 'source_id': 'source',
                       'quote': 'A buyer still reconciles orders manually.', 'url': 'https://example.test/source',
                       'kind': 'problem', 'geography': 'domestic', 'interpretation': 'A problem is described.'}
        with self.store.db:
            self.store.event('old', 'analyze', 'chunk_completed', {'source_ids': ['source'], 'saved': [], 'rejected': 1,
                             'gaps': ['Size of market unknown'], 'agent_observations': [observation], 'batch_index': 0})
        before = research._reported_cycle(self.store, self.rows("SELECT * FROM demand_cycles WHERE id='old'")[0])
        original = self.dump()
        preview = retention.compact(self.store, clock=NOW)
        self.assertEqual(original, self.dump())
        self.assertEqual(preview['cycles_compacted'], 1)
        applied = self.apply()
        self.assertEqual(applied['logical_bytes_after'], preview['logical_bytes_after'])
        self.assertGreater(applied['logical_bytes_reclaimed'], 25000)
        self.assertEqual(applied['source_bodies_pruned'], 1)
        view = research._reported_cycle(self.store, self.rows("SELECT * FROM demand_cycles WHERE id='old'")[0])
        self.assertEqual(view['coverage_view'], 'preserved_at_compaction')
        self.assertEqual(view['state'], before['state'])
        for key in ('read', 'analyzed', 'analysis_gaps', 'analysis_notes', 'rejected_unverified_hypotheses'):
            self.assertEqual(view['coverage'][key], before['coverage'][key])
        self.assertEqual(research._agent_observations(self.store, 'old'), [observation])
        self.assertEqual(self.rows('SELECT * FROM demand_events'), [])
        self.assertNotIn('baseline', view['plan'])
        self.assertEqual(self.rows('SELECT url,content_hash FROM demand_sources')[0]['url'], 'https://example.test/source')
        again = self.apply()
        self.assertEqual(again['cycles_compacted'], 0)
        self.assertEqual(again['logical_bytes_reclaimed'], 0)

    def test_active_recent_pending_and_scheduler_protected_cycles_unchanged(self):
        for identity, options in [('running', {'state': 'running'}), ('export', {'stage': 'export_pending'}),
                                  ('fresh', {'date': NOW.isoformat()}), ('protected', {}), ('pending', {})]:
            self.cycle(identity, **options); self.source(identity, identity)
        with self.store.db:
            self.store.db.execute("UPDATE demand_candidates SET analysis_state='pending' WHERE cycle_id='pending'")
        before = self.dump()
        result = self.apply(protected_cycle_ids={'protected'})
        self.assertEqual(result['cycles_compacted'], 0)
        self.assertEqual(result['source_bodies_pruned'], 0)
        self.assertEqual(self.dump(), before)

    def test_evidence_hypothesis_revision_question_and_live_input_pin_bodies(self):
        self.cycle(); self.cycle('live', state='running')
        for identity in ('evidence', 'hypothesis', 'revision', 'question', 'shared', 'unneeded'):
            self.source(identity=identity)
        with self.store.db:
            self.store.db.execute('''INSERT INTO demand_hypotheses
                (id,job_id,stable_key,track,title,state,payload,created,updated,last_checked,signature)
                VALUES('h','job','key','software','Question','signal',?,?,?,?,'sig')''',
                (json.dumps({'source_ids': ['hypothesis']}), OLD, OLD, OLD))
            self.store.db.execute("INSERT INTO demand_evidence VALUES('e','h','evidence','Quote','Meaning','problem','request','publisher',?)", (OLD,))
            self.store.db.execute("INSERT INTO demand_revisions VALUES('h',1,'old',?,'signal',?)",
                                  (json.dumps({'source_ids': ['revision']}), OLD))
            self.store.db.execute('''INSERT INTO demand_questions
                (id,job_id,track,topic_key,stable_key,depth,state,priority,created_cycle_id,payload,created,updated)
                VALUES('q','job','software','topic','qkey',1,'parked','high','old',?,?,?)''',
                (json.dumps({'source_ids': ['question']}), OLD, OLD))
            self.store.db.execute('''INSERT INTO demand_candidates
                (cycle_id,url,purpose,source_type,state,source_id,analysis_state)
                VALUES('live','https://example.test/shared','support','request','read','shared','done')''')
        original_knowledge = {table: self.rows('SELECT * FROM ' + table) for table in
                              ('demand_hypotheses', 'demand_evidence', 'demand_revisions', 'demand_questions')}
        result = self.apply()
        self.assertEqual(result['source_bodies_pruned'], 1)
        self.assertEqual(self.rows("SELECT id FROM demand_sources WHERE text=''"), [{'id': 'unneeded'}])
        for table, rows in original_knowledge.items():
            self.assertEqual(self.rows('SELECT * FROM ' + table), rows)

    def test_same_hash_real_fetch_restores_tombstone(self):
        self.cycle()
        url = 'https://example.test/source'
        identity = research.digest(url + '\n' + research.digest(research.norm(BODY)))[:24]
        with self.store.db:
            self.store.db.execute('INSERT INTO demand_sources VALUES(?,?,?,?,?,?,?)',
                                  (identity, url, 'Title', BODY, research.digest(research.norm(BODY)), OLD, None))
            self.store.db.execute('''INSERT INTO demand_candidates
                (cycle_id,url,purpose,source_type,state,source_id,analysis_state)
                VALUES('old',?,'support','request','read',?,'done')''', (url, identity))
        self.apply()
        self.assertEqual(self.rows('SELECT text FROM demand_sources')[0]['text'], '')
        self.cycle('live', state='running', stage='fetch', date=NOW.isoformat())
        with self.store.db:
            self.store.db.execute('''INSERT INTO demand_candidates(cycle_id,url,purpose,source_type,state)
                VALUES('live',?,'support','request','pending')''', (url,))
        cycle = self.rows("SELECT * FROM demand_cycles WHERE id='live'")[0]
        with patch.object(research, '_canonical', side_effect=lambda value: value), patch.object(self.store, 'advance', return_value='analyze'):
            research._fetch(self.store, cycle, lambda value: {'ok': True, 'url': url, 'text': BODY, 'fetched_at': NOW.isoformat()})
        self.assertEqual(self.rows('SELECT text FROM demand_sources')[0]['text'], BODY)
        self.assertEqual(self.rows('SELECT fetched_at FROM demand_sources')[0]['fetched_at'], OLD)
        self.assertEqual(self.rows('SELECT * FROM demand_source_retention'), [])

    def test_malformed_json_fails_before_any_change(self):
        self.cycle('first'); self.source('first')
        self.cycle('second')
        with self.store.db:
            self.store.db.execute("UPDATE demand_cycles SET plan='{' WHERE id='second'")
        original = self.dump()
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.dump(), original)

    def test_archive_corruption_fails_closed(self):
        self.cycle(); self.source(); self.apply()
        with self.store.db:
            self.store.db.execute("UPDATE demand_cycle_archives SET sha256='bad'")
        original = self.dump()
        with self.assertRaisesRegex(ValueError, 'archive_invalid'):
            research.execute({'action': 'report', 'id': 'old'}, self.store.path, self.store.cards)
        with self.assertRaisesRegex(ValueError, 'archive_invalid'):
            self.apply()
        self.assertEqual(self.dump(), original)

    def test_error_after_archive_insert_rolls_back_entire_transaction(self):
        self.cycle(); self.source()
        original_dump = self.dump()
        original_read = retention.archived
        def fail_after_insert(db, identity):
            value = original_read(db, identity)
            if value is not None:
                raise ValueError('injected_failure_after_archive_insert')
            return value
        with patch.object(retention, 'archived', side_effect=fail_after_insert):
            with self.assertRaisesRegex(ValueError, 'injected_failure'):
                self.apply()
        self.assertEqual(self.dump(), original_dump)

    def test_no_card_or_export_index_is_changed_and_cached_cycle_is_read_only(self):
        self.cycle(); self.source()
        self.store.cards.mkdir()
        card = self.store.cards / 'manual.md'; card.write_text('Manual edit', encoding='utf-8')
        index = self.store.cards.parent / 'memory-export-index-test.json'; index.write_text('{"manual":true}', encoding='utf-8')
        self.apply()
        with patch.object(research.Research, 'finalize_cards', side_effect=AssertionError('must not re-export')):
            result = research.run_step('job', 'Investigate', self.store.path, self.store.cards, cycle_id='old')
        self.assertTrue(result['cached'])
        self.assertEqual(result['coverage']['coverage_view'], 'preserved_at_compaction')
        self.assertEqual(card.read_text(encoding='utf-8'), 'Manual edit')
        self.assertEqual(index.read_text(encoding='utf-8'), '{"manual":true}')

    def test_team_snapshot_failed_child_and_foreign_lineage_preserved(self):
        self.cycle(version=3); self.source(identity='foreign')
        task = {'key': 'foreign', 'role': 'foreign', 'question': 'Which foreign products solve this buyer problem?',
                'scope': 'Foreign software documentation only.', 'methods': 'Compare verified quotes in product documentation.',
                'queries': ['inventory mismatch software'], 'support_condition': 'Uncovered workflow is documented.',
                'refute_condition': 'Existing solution covers the complete workflow.', 'expected_evidence': 'Quotes from public sources with provenance.',
                'deliverable': 'Evidence, caveats and unresolved questions.'}
        plan = {'objective': 'Evaluate an unmet workflow', 'decision': 'Decide what to test next',
                'rationale': 'Independent sources are needed', 'researchers': []}
        with self.store.db:
            self.store.db.execute("INSERT INTO demand_agent_plans VALUES('old','job','software',?,'final',?)", (json.dumps(plan), OLD))
            self.store.db.execute('''INSERT INTO demand_agent_tasks
                (id,cycle_id,parent_task_id,depth,role,stable_key,question_key,ordinal,status,payload,result,source_ids,reason,created,updated)
                VALUES('child','old','parent',1,'foreign','key','qkey',0,'deferred',?,?,?,'resource_limit',?,?)''',
                (json.dumps(task), json.dumps({'report': {'summary': 'Foreign applicability unknown', 'branch_reason': 'Follow-up still required'}}), json.dumps(['foreign']), OLD, OLD))
            self.store.db.execute("INSERT INTO demand_agent_syntheses VALUES('old',1,'failed',?,?)", (json.dumps({'failed': True, 'summary': 'Not validated', 'conflicts': ['Unresolved']}), OLD))
            self.store.event('old', 'agents', 'synthesis_final', {'result': {'failed': True, 'summary': 'Not validated', 'conflicts': ['Unresolved']}})
        before = agents.snapshot(self.store.db, 'old')
        result = self.apply()
        self.assertEqual(result['cycles_compacted'], 1)
        self.assertEqual(result['source_bodies_pruned'], 0)
        self.assertEqual(agents.snapshot(self.store.db, 'old'), before)
        self.assertEqual(self.rows('SELECT * FROM demand_agent_tasks'), [])
        view = research._reported_cycle(self.store, self.rows("SELECT * FROM demand_cycles WHERE id='old'")[0])
        self.assertEqual(view['state'], 'completed_with_gaps')
        self.assertEqual(view['orchestration']['tasks'][0]['parent_task_id'], 'parent')
        self.assertTrue(view['coverage']['agent_progress']['synthesis_failed'])

    def test_bounded_batches_continue_and_secure_delete_only_on_apply(self):
        for identity in ('first', 'second'):
            self.cycle(identity)
            self.source(identity, identity + '-1')
            self.source(identity, identity + '-2')
        self.store.db.execute('PRAGMA secure_delete=OFF')
        preview = retention.compact(self.store, clock=NOW, max_cycles=1, max_source_bodies=1)
        self.assertEqual(self.store.db.execute('PRAGMA secure_delete').fetchone()[0], 0)
        self.assertTrue(preview['more_eligible'])
        first = self.apply(max_cycles=1, max_source_bodies=1)
        self.assertEqual(self.store.db.execute('PRAGMA secure_delete').fetchone()[0], 1)
        self.assertEqual(first['cycles_compacted'], 1)
        self.assertEqual(first['source_bodies_pruned'], 1)
        self.assertEqual(first['logical_bytes_after'], preview['logical_bytes_after'])
        self.assertTrue(first['more_eligible'])
        for _ in range(3):
            last = self.apply(max_cycles=1, max_source_bodies=1)
        self.assertFalse(last['more_eligible'])
        self.assertEqual(self.rows('SELECT id FROM demand_sources WHERE text<>\'\''), [])

    def test_unknown_or_naive_ages_are_not_deleted(self):
        self.cycle('bad-cycle', date='2026-01-01')
        self.cycle()
        self.source(identity='bad-source', date='not-a-time')
        result = self.apply()
        self.assertEqual(result['cycle_ids'], ['old'])
        self.assertEqual(result['source_bodies_pruned'], 0)


if __name__ == '__main__':
    unittest.main()
