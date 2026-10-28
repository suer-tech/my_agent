import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy import verify_demand_discovery_rescue as probe
from iva_integration import demand_research as research, demand_agents as agents
from tests.test_demand_agents import research_plan
from tests.test_demand_research import canonical


class NativeFixtureClient:
    """No model: drive the real stage/checkpoint contracts in isolated tests."""
    def __init__(self, stage, all_calls):
        self.stage, self.all_calls = stage, all_calls
        self.last_execution = {'model': 'fake-test-only', 'reasoning_effort': 'medium' if stage == 'agent_report' else 'low',
                               'read_only': True, 'web_search': 'live' if stage == 'discovery' else 'disabled'}
        self.last_web_events = []

    def structured(self, prompt, schema):
        self.all_calls.append(self.stage)
        if self.stage == 'discovery':
            self.last_web_events = [{'type': 'web_search', 'query': 'inventory import alternatives',
                                     'results': [{'url': 'https://open.test/discussion', 'title': 'Discussion'}]}]
            return {'queries': ['inventory import alternatives'], 'candidates': [
                {'url': 'https://open.test/discussion', 'purpose': 'support', 'source_type': 'discussion'}], 'limitations': []}
        if self.stage == 'analyze':
            return {'hypotheses': [], 'gaps': ['Synthetic test text proves no market demand.'], 'followups': [],
                    'branch_answer': {'verdict': 'inconclusive', 'basis': 'No active question in this fixture.', 'source_ids': [], 'evidence': []}}
        if self.stage == 'agent_report':
            return {'summary': 'Read one accessible replacement source; no demand conclusion follows.', 'findings': [],
                    'conflicts': [], 'limitations': ['Unit fixture is synthetic.'], 'child_requests': [],
                    'branch_reason': 'No actual market evidence in this synthetic fixture warrants a child task.'}
        raise AssertionError('Unexpected stage or coordinator invocation')


class DiscoveryRescueProbe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'source.sqlite3'
        self.url_patch = patch.object(research, '_canonical', side_effect=canonical)
        self.url_patch.start(); self.addCleanup(self.url_patch.stop)
        store = research.Research(self.database, self.root / 'source-cards')
        try:
            cycle = store.cycle('probe-test', 'Research only software workflows.')
            self.cycle = cycle['id']
            with store.db:
                agents.save_plan(store.db, self.cycle, cycle['job_id'], 'software', research_plan())
                self.task = store.rows("SELECT id FROM demand_agent_tasks WHERE role='foreign'")[0]['id']
                store.db.execute("UPDATE demand_agent_tasks SET status='done',result='{}'")
                store.db.execute("UPDATE demand_agent_plans SET status='done'")
                store.db.execute("INSERT INTO demand_batches(cycle_id,batch_index,focus,state,discovery,agent_task_id) VALUES(?,0,'foreign test','done',?,?)",
                                 (self.cycle, json.dumps({'queries': ['prior actual discovery'], 'new_candidates': 5}), self.task))
                for i in range(5):
                    store.db.execute("INSERT INTO demand_candidates(cycle_id,url,purpose,source_type,state,error,batch_index,geography) VALUES(?,?,'support','discussion','failed','access_denied',0,'international')",
                                     (self.cycle, 'https://closed.test/' + str(i)))
                store.event(self.cycle, 'agents', 'role_report', {'task_id': self.task, 'report': {'summary': 'No sources read'}, 'source_ids': []})
                store.event(self.cycle, 'agents', 'review_completed', {'task_id': self.task})
                store.db.execute("UPDATE demand_cycles SET state='completed_with_gaps',stage='done',result='{}' WHERE id=?", (self.cycle,))
        finally:
            store.close()

    def prepare(self):
        directory = self.root / 'case'
        fixture = probe.prepare_case(self.database, directory, self.cycle, self.task)
        return directory, fixture

    def test_normal_stages_repair_copy_and_stop_before_review(self):
        original = hashlib.sha256(self.database.read_bytes()).hexdigest()
        directory, fixture = self.prepare()
        calls = []
        def fetch(url):
            return {'ok': True, 'url': url, 'canonical_url': url, 'title': 'Public fixture',
                    'text': 'Public source body for isolated diagnostic: importing inventory requires manual reconciliation after changes. ' * 2}
        report = probe.run_fixture(fixture, directory, lambda stage: NativeFixtureClient(stage, calls), fetch, lambda _: None)
        self.assertTrue(report['passed'], report['criteria'])
        self.assertEqual(calls, ['discovery', 'analyze', 'agent_report'])
        self.assertEqual(report['read_sources'], 1)
        self.assertFalse(report['full_cycle'])
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), original)
        self.assertFalse((self.root / 'source-cards').exists())
        self.assertFalse((directory / 'cards').exists())

    def test_running_source_is_refused_before_discovery(self):
        store = research.Research(self.database, self.root / 'source-cards')
        with store.db:
            store.db.execute("UPDATE demand_cycles SET state='running',stage='analyze'")
        store.close()
        with self.assertRaisesRegex(RuntimeError, 'historical_cycle_not_terminal'):
            self.prepare()

    def test_existing_read_source_is_not_falsely_represented_as_empty(self):
        store = research.Research(self.database, self.root / 'source-cards')
        with store.db:
            store.db.execute("UPDATE demand_candidates SET state='read',source_id='real-source' WHERE url='https://closed.test/0'")
        store.close()
        with self.assertRaisesRegex(RuntimeError, 'historical_five_failed_sources_required'):
            self.prepare()

    def test_failed_rescue_is_reported_as_failure_not_fabricated_success(self):
        directory, fixture = self.prepare()
        calls = []
        report = probe.run_fixture(fixture, directory, lambda stage: NativeFixtureClient(stage, calls),
                                  lambda url: {'ok': False, 'error_category': 'access_denied'}, lambda _: None)
        self.assertFalse(report['passed'])
        self.assertFalse(report['criteria']['new_sources_actually_read'])
        self.assertFalse(report['criteria']['one_fresh_report_with_real_sources'])
        self.assertEqual(calls, ['discovery', 'agent_report'])
        self.assertTrue(report['criteria']['persisted_rescue_not_duplicated'])


if __name__ == '__main__':
    unittest.main()
