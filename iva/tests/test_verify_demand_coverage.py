"""Native probe guards and real stage contracts; tests never call a model/site."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy import verify_demand_coverage as probe
from iva_integration import demand_research as research, demand_agents as agents
from tests.test_demand_agents import research_plan
from tests.test_demand_research import canonical


class FixtureClient:
    def __init__(self, stage, calls):
        self.stage, self.calls = stage, calls
        self.last_execution = {'model': 'fake-test-only', 'reasoning_effort': 'medium',
                               'read_only': True, 'web_search': 'live' if stage == 'discovery' else 'disabled'}
        self.last_web_events = []

    def structured(self, prompt, schema):
        self.calls.append(self.stage)
        if self.stage == 'agent_coverage':
            return {'criteria': [{'key': key, 'status': 'missing',
                                 'reason': 'No verified direct user history answers this question in the isolated fixture.',
                                 'witnesses': []} for key in ('assignment', 'user_experience', 'solution')],
                    'queries': ['inventory reservation manual reconciliation user reviews',
                                'warehouse connector reservation user experience'], 'limitations': []}
        if self.stage == 'discovery':
            self.last_web_events = [{'type': 'web_search', 'query': 'inventory reservation user reviews',
                                     'results': [{'url': 'https://open.test/review', 'title': 'Public review'}]}]
            return {'queries': ['inventory reservation user reviews'], 'candidates': [
                {'url': 'https://open.test/review', 'purpose': 'support', 'source_type': 'review'}], 'limitations': []}
        if self.stage == 'analyze':
            return {'hypotheses': [], 'gaps': ['Synthetic test text proves no market demand.'], 'followups': [],
                    'branch_answer': {'verdict': 'inconclusive', 'basis': 'No active question in this fixture.', 'source_ids': [], 'evidence': []}}
        if self.stage == 'agent_report':
            return {'summary': 'The public pricing page and isolated extra source do not establish independent user evidence.',
                    'findings': [], 'conflicts': [], 'limitations': ['Evidence remains insufficient; the URL budget is exhausted.'],
                    'child_requests': [], 'branch_reason': 'No actual market evidence in this synthetic fixture warrants a child task.'}
        raise AssertionError('Unexpected coordinator or model stage')


class CoverageProbe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'source.sqlite3'
        self.url_patch = patch.object(research, '_canonical', side_effect=canonical)
        self.url_patch.start(); self.addCleanup(self.url_patch.stop)
        store = research.Research(self.database, self.root / 'source-cards')
        try:
            cycle = store.cycle('coverage-probe-test', 'Research only software workflows.')
            self.cycle = cycle['id']
            with store.db:
                agents.save_plan(store.db, self.cycle, cycle['job_id'], 'software', research_plan())
                self.task = store.rows("SELECT id FROM demand_agent_tasks WHERE role='foreign'")[0]['id']
                store.db.execute("UPDATE demand_agent_tasks SET status='done',result='{}'")
                store.db.execute("UPDATE demand_agent_tasks SET status='running',result=NULL WHERE id=?", (self.task,))
                plan = json.loads(cycle['plan']); plan.pop('evidence_coverage_version', None)
                store.db.execute("UPDATE demand_cycles SET stage='agent_review',plan=? WHERE id=?", (research.dumps(plan), self.cycle))
                for batch in (0, 1):
                    store.db.execute("INSERT INTO demand_batches(cycle_id,batch_index,focus,state,discovery,agent_task_id) VALUES(?,?,'foreign test','done',?,?)",
                                     (self.cycle, batch, json.dumps({'queries': ['prior actual discovery'], 'new_candidates': 5 + batch}), self.task))
                    for i in range(5):
                        store.db.execute("INSERT INTO demand_candidates(cycle_id,url,purpose,source_type,state,error,batch_index,geography) VALUES(?,?,'support','discussion','failed','access_denied',?,'international')",
                                         (self.cycle, f'https://closed{batch}.test/{i}', batch))
                body = 'Public Cin7 pricing page lists plans and payment terms but no independent user experience. ' * 2
                store.db.execute('INSERT INTO demand_sources VALUES(?,?,?,?,?,?,?)',
                                 ('pricing', 'https://www.cin7.com/pricing/', 'Pricing', body, research.digest(body), research.now(), None))
                store.db.execute("INSERT INTO demand_candidates(cycle_id,url,purpose,source_type,state,source_id,batch_index,analysis_state,geography) VALUES(?,'https://www.cin7.com/pricing/','alternative','offer','read','pricing',1,'done','international')", (self.cycle,))
                store.event(self.cycle, 'agents', 'discovery_rescue_scheduled', {'task_id': self.task, 'batch_index': 1,
                    'original_batch_index': 0, 'attempt': 1, 'remaining_urls': 7, 'failed_urls': [], 'closed_hosts': ['closed0.test']})
                store.event(self.cycle, 'agents', 'role_report', {'task_id': self.task, 'report': {'summary': 'Only vendor pricing read'}, 'source_ids': ['pricing']})
        finally:
            store.close()

    def prepare(self):
        directory = self.root / 'case'
        fixture = probe.prepare_case(self.database, directory, self.cycle, self.task)
        return directory, fixture

    def test_pricing_insufficiency_runs_bounded_targeted_search_then_report(self):
        original = hashlib.sha256(self.database.read_bytes()).hexdigest()
        directory, fixture = self.prepare()
        calls = []
        report = probe.run_fixture(fixture, directory, lambda stage: FixtureClient(stage, calls),
            lambda url: {'ok': True, 'url': url, 'canonical_url': url, 'title': 'Fixture public source',
                         'text': 'A public fixture body describing manual inventory reconciliation, with no real customer evidence. ' * 2},
            lambda _: None)
        self.assertTrue(report['passed'], report['criteria'])
        self.assertEqual(calls[0], 'agent_coverage')
        self.assertEqual(calls[-1], 'agent_report')
        self.assertEqual(calls.count('discovery'), 1)
        self.assertEqual(report['new_candidates'], 1)
        self.assertEqual(report['closed'][0]['status'], 'deferred')
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), original)
        self.assertFalse((directory / 'cards').exists())

    def test_blocked_replacement_is_honest_gap_not_false_recovery(self):
        directory, fixture = self.prepare(); calls = []
        report = probe.run_fixture(fixture, directory, lambda stage: FixtureClient(stage, calls),
                                  lambda url: {'ok': False, 'error_category': 'access_denied'}, lambda _: None)
        self.assertTrue(report['passed'], report['criteria'])
        self.assertEqual(report['read_sources'], 1)
        self.assertEqual(report['new_source_urls'], [])
        self.assertEqual(report['closed'][0]['status'], 'deferred')
        self.assertEqual(calls.count('discovery'), 1)

    def test_wrong_boundary_is_refused(self):
        store = research.Research(self.database, self.root / 'source-cards')
        with store.db:
            store.db.execute("UPDATE demand_cycles SET stage='fetch'")
        store.close()
        with self.assertRaisesRegex(RuntimeError, 'seed_must_be_previous_probe_report_boundary'):
            self.prepare()

    def test_more_than_one_read_source_is_not_misrepresented_as_pricing_only(self):
        store = research.Research(self.database, self.root / 'source-cards')
        with store.db:
            store.db.execute("UPDATE demand_candidates SET state='read',source_id='pricing',analysis_state='done' WHERE url='https://closed0.test/0'")
        store.close()
        with self.assertRaisesRegex(RuntimeError, 'seed_one_read_page_and_ten_failures_required'):
            self.prepare()

    def test_previous_coverage_optin_cannot_be_reset(self):
        store = research.Research(self.database, self.root / 'source-cards')
        with store.db:
            plan = json.loads(store.rows('SELECT plan FROM demand_cycles')[0]['plan'])
            plan['evidence_coverage_version'] = 1
            store.db.execute('UPDATE demand_cycles SET plan=?', (research.dumps(plan),))
        store.close()
        with self.assertRaisesRegex(RuntimeError, 'seed_previous_contract_changed'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
