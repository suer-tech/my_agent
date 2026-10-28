import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from deploy import verify_demand_review as diagnostic
from iva_integration import demand_agents as agents
from iva_integration import demand_research as research
from tests.test_demand_agents import research_plan, child


class ReviewProbe(unittest.TestCase):
    def fixture(self, directory, nonempty=False, grandchild=False, source_size=None):
        source = directory / 'source.sqlite3'
        store = research.Research(source, directory / 'source-cards')
        try:
            cycle = store.cycle('isolated-fixture', 'Test public research')
            with store.db:
                # This verifier rewinds pre-scope historical production cycles.
                # A fresh cycle now has an independent scope audit; retaining it
                # would test a different multi-call stage with the old fixture.
                historical_plan = json.loads(cycle['plan'])
                historical_plan.pop('scope', None)
                store.db.execute('UPDATE demand_cycles SET plan=? WHERE id=?',
                                 (json.dumps(historical_plan), cycle['id']))
                view = agents.save_plan(store.db, cycle['id'], cycle['job_id'], cycle['track'], research_plan())
                selected = next(row for row in view['tasks'] if row['role'] == ('critic' if nonempty else 'buyer_problem'))
                excerpt = child()['trigger_quote']
                if source_size is not None:
                    excerpt += 'x' * (source_size - len(excerpt))
                store.db.execute('INSERT INTO demand_sources VALUES(?,?,?,?,?,?,?)',
                                 ('s1', 'https://example.test/story', 'Fixture source', excerpt,
                                  research.digest(research.norm(excerpt)), research.now(), None))
                if grandchild:
                    store.db.execute("UPDATE demand_agent_tasks SET status='running' WHERE id=?", (selected['id'],))
                    # A real-read source already belongs to the cycle before a
                    # copied historical parent is constructed for this test.
                    store.db.execute("INSERT INTO demand_candidates(cycle_id,url,purpose,source_type,state,source_id,batch_index,analysis_state) VALUES(?,'https://example.test/story','support','discussion','read','s1',0,'done')", (cycle['id'],))
                    admitted = agents.enqueue_children(store.db, selected['id'], [child('historical-parent', 'critic')], {'s1'})
                    selected = agents.task(store.db, admitted['admitted'][0])
                store.db.execute("INSERT INTO demand_batches(cycle_id,batch_index,focus,state,agent_task_id) VALUES(?,0,'Fixture','done',?)",
                                 (cycle['id'], selected['id']))
                if not grandchild:
                    store.db.execute("INSERT INTO demand_candidates(cycle_id,url,purpose,source_type,state,source_id,batch_index,analysis_state) VALUES(?,'https://example.test/story','support','discussion','read','s1',0,'done')", (cycle['id'],))
                requests = [child()] if nonempty else []
                if grandchild:
                    requests[0]['trigger_quote'] = '«' + requests[0]['trigger_quote'] + '»'
                if nonempty and not grandchild:
                    store.db.execute("UPDATE demand_agent_tasks SET status='running' WHERE id=?", (selected['id'],))
                    agents.enqueue_children(store.db, selected['id'], requests, {'s1'})
                store.db.execute("UPDATE demand_agent_tasks SET status='done',result='{}' WHERE cycle_id=?", (cycle['id'],))
                store.db.execute("UPDATE demand_agent_plans SET status='complete' WHERE cycle_id=?", (cycle['id'],))
                store.db.execute("UPDATE demand_cycles SET state='completed_with_gaps',stage='done',result='{}' WHERE id=?", (cycle['id'],))
                store.event(cycle['id'], 'agents', 'role_report', {'task_id': selected['id'], 'source_ids': ['s1'],
                    'report': {'summary': 'Fixture report', 'findings': [], 'conflicts': [], 'limitations': [], 'child_requests': requests}})
                store.db.execute("INSERT INTO demand_agent_syntheses VALUES(?,1,'final','{}',?)", (cycle['id'], research.now()))
                store.event(cycle['id'], 'agents', 'synthesis_final', {'result': {}})
            return source, cycle['id'], selected['id']
        finally:
            store.close()

    def test_readonly_backup_includes_wal_and_does_not_change_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / 'original.db', root / 'copy.db'
            original = sqlite3.connect(source)
            try:
                original.execute('PRAGMA journal_mode=WAL')
                original.execute('CREATE TABLE proof(value TEXT)')
                original.execute("INSERT INTO proof VALUES('committed in WAL')")
                original.commit()
                diagnostic.readonly_backup(source, target)
                with closing(sqlite3.connect(target)) as copied:
                    self.assertEqual(copied.execute('SELECT value FROM proof').fetchone()[0], 'committed in WAL')
                    copied.execute("INSERT INTO proof VALUES('copy only')")
                    copied.commit()
                self.assertEqual(original.execute('SELECT count(*) FROM proof').fetchone()[0], 1)
                with self.assertRaises(ValueError):
                    diagnostic.readonly_backup(source, source)
            finally:
                original.close()

    def test_rewind_changes_copy_only_and_preserves_historical_children(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, cycle, task = self.fixture(root, nonempty=True)
            fixture = diagnostic.prepare_case(source, root / 'case', cycle, task, False)
            self.assertEqual(len(fixture['historical_child_ids']), 1)
            with closing(sqlite3.connect(source)) as original:
                self.assertEqual(original.execute('SELECT stage FROM demand_cycles').fetchone()[0], 'done')
                self.assertEqual(original.execute('SELECT count(*) FROM demand_agent_syntheses').fetchone()[0], 1)
            store = research.Research(root / 'case/research.sqlite3', root / 'case/cards')
            try:
                self.assertEqual(agents.task(store.db, task)['status'], 'running')
                self.assertEqual(store.db.execute('SELECT count(*) FROM demand_agent_syntheses').fetchone()[0], 0)
                self.assertEqual(agents.task(store.db, fixture['historical_child_ids'][0])['status'], 'done')
                self.assertTrue(all(row['verified_quote_and_sources'] for row in diagnostic.request_gates(store, fixture)))
                fixture['report']['report']['child_requests'][0]['trigger_quote'] = 'Invented words without any real source.'
                self.assertFalse(diagnostic.request_gates(store, fixture)[0]['verified_quote_and_sources'])
            finally:
                store.close()

    def test_empty_and_nonempty_real_path_fixtures_do_not_force_approval_or_duplicate(self):
        class Client:
            last_web_events = []
            last_execution = {'model': 'unit-test-only', 'reasoning_effort': 'medium', 'web_search': 'disabled', 'read_only': True}
            def __init__(self, keys, disposition):
                self.keys, self.disposition = keys, disposition
            def structured(self, prompt, schema):
                return {'summary': 'Review only the actually submitted requests.',
                        'decisions': [{'key': key, 'decision': self.disposition, 'reason': 'A bounded review of the quoted source.'} for key in self.keys]}
        with tempfile.TemporaryDirectory() as temp:
            for index, (nonempty, disposition) in enumerate([(False,'reject'), (True,'approve'), (True,'reject'), (True,'defer')]):
                with self.subTest(nonempty=nonempty, disposition=disposition):
                    root = Path(temp) / str(index)
                    root.mkdir()
                    source, cycle, task = self.fixture(root, nonempty)
                    fixture = diagnostic.prepare_case(source, root / 'case', cycle, task, not nonempty)
                    keys = [item['key'] for item in fixture['report']['report']['child_requests']]
                    result = diagnostic.run_case(fixture, root / 'case', Client(keys, disposition))
                    self.assertTrue(result['passed'], result['criteria'])
                    self.assertEqual(result['review']['decisions'], [] if not nonempty else
                                     [{'key': 'integration', 'decision': disposition, 'reason': 'A bounded review of the quoted source.'}])
                    self.assertEqual(result['new_task_ids'], [])
                    self.assertFalse(result['full_cycle'])

    def test_fixed_output_rejects_live_path_and_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            output = diagnostic.paths(release, release / 'verification')
            with self.assertRaises(ValueError):
                diagnostic.paths(release, release / 'production')
            (output / 'source-snapshot.sqlite3').write_bytes(b'preserve')
            with self.assertRaisesRegex(RuntimeError, 'fresh_directory'):
                diagnostic.paths(release, output)

    def test_grandchild_admission_normalizes_verified_quote_and_labels_nonapproval(self):
        class Client:
            last_web_events = []
            last_execution = {'model': 'unit-test-only', 'reasoning_effort': 'medium', 'web_search': 'disabled', 'read_only': True}
            def __init__(self, disposition):self.disposition = disposition
            def structured(self, prompt, schema):
                return {'summary': 'A bounded review of the quoted referral condition.',
                        'decisions': [{'key': 'integration', 'decision': self.disposition, 'reason': 'The verified condition warrants this explicit decision.'}]}
        with tempfile.TemporaryDirectory() as temp:
            for disposition in ('approve', 'defer', 'reject'):
                with self.subTest(disposition=disposition):
                    root = Path(temp) / disposition
                    root.mkdir()
                    source, cycle, task = self.fixture(root, nonempty=True, grandchild=True)
                    fixture = diagnostic.prepare_case(source, root / 'case', cycle, task, False, True)
                    result = diagnostic.run_case(fixture, root / 'case', Client(disposition))
                    self.assertTrue(result['passed'], result['criteria'])
                    probe = result['grandchild_probe']
                    self.assertEqual(probe['admission_exercised'], disposition == 'approve')
                    self.assertFalse(probe['child_search_executed'])
                    if disposition != 'reject':
                        self.assertTrue(probe['normalized_exact_quote_and_depth_two'])
                        self.assertEqual(len(probe['created_task_ids']), 1)
                    if disposition != 'approve':
                        self.assertTrue(probe['missing_coverage'])

    def test_fresh_report_rewinds_only_selected_copy_report_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source,cycle,task=self.fixture(root,nonempty=True,grandchild=True,source_size=2048)
            store=research.Research(source,root/'source-cards')
            try:
                other=store.rows('SELECT id FROM demand_agent_tasks WHERE cycle_id=? AND id<>? LIMIT 1',(cycle,task))[0]['id']
                with store.db:
                    store.event(cycle,'agents','role_report',{'task_id':other,'report':{'child_requests':[]}})
                    store.event(cycle,'agents','review_completed',{'task_id':task,'review':{'summary':'Historical review','decisions':[]}})
            finally:store.close()
            fixture=diagnostic.prepare_case(source,root/'fresh',cycle,task,False,True,fresh_report=True)
            self.assertTrue(fixture['fresh_report'])
            self.assertEqual(len(fixture['archive']['selected_report_review_events']),2)
            store=research.Research(root/'fresh/research.sqlite3',root/'fresh/cards')
            try:
                self.assertEqual(store.rows('SELECT stage FROM demand_cycles WHERE id=?',(cycle,))[0]['stage'],'agent_report')
                self.assertEqual(research._agent_events(store,cycle,'role_report',task),[])
                self.assertEqual(research._agent_events(store,cycle,'review_completed',task),[])
                self.assertEqual(len(research._agent_events(store,cycle,'role_report',other)),1)
            finally:store.close()
            with closing(sqlite3.connect(source)) as original:
                self.assertEqual(original.execute('SELECT stage FROM demand_cycles WHERE id=?',(cycle,)).fetchone()[0],'done')
                self.assertEqual(original.execute("SELECT count(*) FROM demand_events WHERE event='role_report'").fetchone()[0],2)

    def test_fresh_report_calls_once_preserves_2048_context_and_stops_before_review(self):
        class Client:
            last_web_events=[]
            last_execution={'model':'unit-test-only','reasoning_effort':'medium','web_search':'disabled','read_only':True}
            def __init__(self,propose):self.calls=0;self.propose=propose
            def structured(self,prompt,schema):
                self.calls+=1
                self.asserted_schema=schema
                return {'summary':'The saved referral source is available in full for this report.',
                        'findings':[],'conflicts':[],'limitations':[],
                        'child_requests':[child('fresh-open-question')] if self.propose else [],
                        'branch_reason':'The report considers consequential unknown referral conditions without requiring a fixed number of new tasks.'}
        with tempfile.TemporaryDirectory() as temp:
            for propose in (False,True):
                root=Path(temp)/str(propose);root.mkdir()
                source,cycle,task=self.fixture(root,nonempty=True,grandchild=True,source_size=2048)
                fixture=diagnostic.prepare_case(source,root/'fresh',cycle,task,False,True,fresh_report=True)
                client=Client(propose)
                result=diagnostic.run_case(fixture,root/'fresh',client)
                self.assertTrue(result['passed'],result['criteria'])
                self.assertEqual(client.calls,1)
                self.assertEqual(result['checkpoint_stage'],'agent_review')
                self.assertEqual(result['source_context']['pages_2048'],{'present':1,'complete':1})
                self.assertEqual(result['source_context']['sources'][0]['provided_characters'],2048)
                self.assertFalse(result['source_context']['retained_prompt_or_source_text'])
                self.assertEqual(result['role_report']['child_requests'],[child('fresh-open-question')] if propose else [])
                self.assertFalse(result['model_reexecuted_on_checkpoint_read'])
                self.assertFalse((root/'fresh/cards').exists())
                with closing(sqlite3.connect(root/'fresh/research.sqlite3')) as copied:
                    selected=copied.execute('SELECT result,status FROM demand_agent_tasks WHERE id=?',(task,)).fetchone()
                    self.assertEqual(selected,(None,'running'))
                    self.assertEqual(copied.execute("SELECT count(*) FROM demand_events WHERE event='review_completed' AND id>?",(fixture['event_floor'],)).fetchone()[0],0)

    def test_report_context_probe_rejects_truncation_and_retains_only_metadata(self):
        sources={'s1':{'id':'s1','url':'https://example.test/source','title':'Public source','text':'SOURCE_TEXT_MUST_NOT_BE_LOGGED_'+'x'*2017}}
        context=research._report_source_context(sources)
        prompt='Probe\nДоступные source_ids и выдержки: '+research.dumps(context)
        good=diagnostic.report_context_metadata(prompt,sources)
        self.assertTrue(good['valid_complete_json'])
        self.assertTrue(good['metadata_matches_stored_sources'])
        self.assertNotIn('SOURCE_TEXT_MUST_NOT_BE_LOGGED_',json.dumps(good))
        self.assertFalse(diagnostic.report_context_metadata(prompt[:-1],sources)['valid_complete_json'])
        tampered=json.loads(research.dumps(context));tampered[0]['text']='changed'
        bad=diagnostic.report_context_metadata('Probe\nДоступные source_ids и выдержки: '+research.dumps(tampered),sources)
        self.assertFalse(bad['metadata_matches_stored_sources'])

    def test_four_independent_cases_start_with_fresh_report(self):
        self.assertEqual(list(diagnostic.CASES),['fresh_report','empty','nonempty','decorative_quote'])
        self.assertEqual(diagnostic.CASES['fresh_report'],diagnostic.CASES['decorative_quote'])
        self.assertNotEqual(diagnostic.CASES['fresh_report'],diagnostic.CASES['empty'])


if __name__ == '__main__':
    unittest.main()
