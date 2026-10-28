"""Alternative-source recovery uses persisted normal stages and strict budgets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import demand_agents as agents
from iva_integration import demand_discovery_rescue as rescue
from iva_integration import demand_research as dr
from tests.test_demand_agents import research_plan
from tests.test_demand_research import AgentCli, FakeCli, canonical, deep_quote


class DiscoveryRescue(unittest.TestCase):
    def setUp(self):
        # This suite exercises saved v3 cycles predating evidence coverage.
        coverage_patch = patch.object(dr, 'EVIDENCE_COVERAGE_ENABLED', False)
        coverage_patch.start()
        self.addCleanup(coverage_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'research.sqlite3'
        self.cards = Path(self.tmp.name) / 'cards'
        self.patch = patch.object(dr, '_canonical', side_effect=canonical)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.store = dr.Research(self.path, self.cards)
        self.addCleanup(lambda: self.store.close())
        self.cycle = self.store.cycle('rescue-test', 'Исследовать только программные рабочие процессы.')
        with self.store.db:
            agents.save_plan(self.store.db, self.cycle['id'], self.cycle['job_id'], 'software', research_plan())
            self.store.advance(self.cycle)
        self.refresh()
        self.task = agents.next_task(self.store.db, self.cycle['id'])
        self.cli = FakeCli()

    def refresh(self):
        self.cycle = self.store.rows('SELECT * FROM demand_cycles WHERE id=?', (self.cycle['id'],))[0]

    def original(self, count=3, fail='access_denied'):
        self.cli.discovery['candidates'] = [
            {'url': f'https://closed.test/topic/{i}', 'purpose': 'support', 'source_type': 'discussion'}
            for i in range(count)]
        dr._discovery(self.store, self.cycle, self.cli)
        self.refresh()
        if count:
            while self.cycle['stage'] == 'fetch':
                dr._fetch(self.store, self.cycle, lambda url: {'ok': False, 'error_category': fail})
                self.refresh()
        self.refresh()

    def marker(self):
        return dr._agent_events(self.store, self.cycle['id'], rescue.EVENT, self.task['id'])

    def test_all_denied_appends_same_task_batch_and_survives_restart_once(self):
        self.original()
        self.assertEqual(self.cycle['stage'], 'discovery')
        marker = self.marker()
        self.assertEqual(len(marker), 1)
        self.assertEqual(marker[0]['closed_hosts'], ['closed.test'])
        before = self.store.rows('SELECT * FROM demand_batches ORDER BY batch_index')
        self.assertEqual([b['state'] for b in before], ['done', 'pending'])
        self.assertEqual({b['agent_task_id'] for b in before}, {self.task['id']})
        self.store.close()
        self.store = dr.Research(self.path, self.cards)
        self.refresh()
        with self.store.db:
            self.assertEqual(self.store.advance(self.cycle), 'discovery')
        self.assertEqual(self.store.rows('SELECT * FROM demand_batches ORDER BY batch_index'), before)
        self.assertEqual(self.marker(), marker)

    def test_old_urls_and_closed_hosts_are_rejected_but_other_hosts_allowed(self):
        self.original()
        original = self.store.rows('SELECT * FROM demand_batches WHERE batch_index=0')[0]
        urls = ['https://closed.test/topic/0', 'https://closed.test/new', 'https://open.test/topic']
        self.cli.discovery['candidates'] = [
            {'url': url, 'purpose': 'support', 'source_type': 'discussion'} for url in urls]
        dr._discovery(self.store, self.cycle, self.cli)
        new = self.store.rows('SELECT url FROM demand_candidates WHERE batch_index=1')
        self.assertEqual(new, [{'url': urls[-1]}])
        self.assertIn('единственный дополнительный поиск', self.cli.calls[-1])
        self.assertIn('Партия источников №2:', self.cli.calls[-1])
        self.assertNotIn('Текущая партия ', self.cli.calls[-1])
        self.assertIn('closed.test', self.cli.calls[-1])
        self.assertEqual(self.store.rows('SELECT * FROM demand_batches WHERE batch_index=0')[0], original)

    def test_empty_rescue_or_failed_rescue_ends_at_report_without_third_search(self):
        for empty in (True, False):
            with self.subTest(empty=empty):
                if not empty:
                    self.tearDown_for_subcase()
                self.original()
                self.cli.discovery['candidates'] = [] if empty else [
                    {'url': 'https://other.test/closed', 'purpose': 'support', 'source_type': 'discussion'}]
                dr._discovery(self.store, self.cycle, self.cli)
                self.refresh()
                if not empty:
                    dr._fetch(self.store, self.cycle, lambda url: {'ok': False, 'error_category': 'access_challenge'})
                    self.refresh()
                self.assertEqual(self.cycle['stage'], 'agent_report')
                self.assertEqual(len(self.marker()), 1)
                self.assertEqual(len(self.store.rows('SELECT * FROM demand_batches')), 2)

    def tearDown_for_subcase(self, foreign_first=False):
        # A separate database makes both independent scenarios visible to unittest.
        self.store.close()
        self.path = Path(self.tmp.name) / 'other.sqlite3'
        self.store = dr.Research(self.path, self.cards)
        self.cycle = self.store.cycle('rescue-test', 'Исследовать только программные рабочие процессы.')
        with self.store.db:
            plan = research_plan()
            if foreign_first:
                plan['researchers'].sort(key=lambda item: item['role'] != 'foreign')
            agents.save_plan(self.store.db, self.cycle['id'], self.cycle['job_id'], 'software', plan)
            self.store.advance(self.cycle)
        self.refresh()
        self.task = agents.next_task(self.store.db, self.cycle['id'])
        self.cli = FakeCli()

    def test_task_and_global_url_limits_include_original_batch(self):
        self.original(count=10)
        self.assertEqual(self.marker()[0]['remaining_urls'], 2)
        self.cli.discovery['candidates'] = [
            {'url': f'https://open.test/topic/{i}', 'purpose': 'support', 'source_type': 'discussion'}
            for i in range(10)]
        dr._discovery(self.store, self.cycle, self.cli)
        self.assertEqual(rescue.task_url_count(self.store, self.cycle['id'], self.task['id']), 12)

    def test_blocked_first_proposal_does_not_consume_two_remaining_slots(self):
        self.original(count=10)
        self.cli.discovery['candidates'] = [
            {'url': url, 'purpose': 'support', 'source_type': 'discussion'} for url in
            ('https://closed.test/new', 'https://open.test/one', 'https://open.test/two', 'https://open.test/extra')]
        dr._discovery(self.store, self.cycle, self.cli)
        self.assertEqual([r['url'] for r in self.store.rows('SELECT url FROM demand_candidates WHERE batch_index=1 ORDER BY rowid')],
                         ['https://open.test/one', 'https://open.test/two'])
        self.assertEqual(rescue.task_url_count(self.store, self.cycle['id'], self.task['id']), 12)

    def test_exhausted_global_budget_does_not_schedule_or_invoke_search(self):
        plan = json.loads(self.cycle['plan']); plan['max_urls'] = 3
        with self.store.db:
            self.store.db.execute('UPDATE demand_cycles SET plan=? WHERE id=?', (dr.dumps(plan), self.cycle['id']))
        self.refresh(); self.original()
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(self.marker(), [])
        self.assertEqual(len(self.cli.calls), 1)

    def test_exhausted_role_budget_does_not_schedule(self):
        self.original(count=12)
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(self.marker(), [])

    def test_any_read_source_suppresses_rescue(self):
        dr._discovery(self.store, self.cycle, self.cli); self.refresh()
        dr._fetch(self.store, self.cycle, lambda url: {'ok': True, 'url': url, 'text': 'A verified public source body. ' * 5})
        with self.store.db:
            self.store.db.execute("UPDATE demand_candidates SET analysis_state='done'")
            self.store.advance(self.cycle)
        self.refresh()
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(self.marker(), [])

    def test_empty_initial_discovery_gets_only_one_more_attempt(self):
        self.original(count=0)
        self.assertEqual(self.cycle['stage'], 'discovery')
        self.assertEqual(self.marker()[0]['failed_urls'], [])
        dr._discovery(self.store, self.cycle, self.cli); self.refresh()
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(len(self.cli.calls), 2)

    def test_technical_discovery_failure_has_no_rescue(self):
        with self.store.db:
            self.store.db.execute("UPDATE demand_batches SET state='failed',error='schema_invalid'")
            self.store.advance(self.cycle)
        self.refresh()
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(self.marker(), [])

    def test_exhausted_rescue_model_failure_remains_failed_after_report_review(self):
        self.original()
        for attempt in range(3):
            self.store.fail(self.cycle, 'Isolated model failure', 'schema_invalid')
            self.refresh()
        self.assertEqual(self.cycle['stage'], 'agent_report')
        model = AgentCli()
        dr._agent_report(self.store, self.cycle, model); self.refresh()
        dr._agent_review(self.store, self.cycle, model); self.refresh()
        task = agents.task(self.store.db, self.task['id'])
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['result']['discovery_failures'], [{'batch_index': 1, 'category': 'schema_invalid'}])
        self.assertTrue(task['result']['report']['summary'])
        self.assertEqual(len(self.marker()), 1)
        self.assertEqual(len(self.store.rows('SELECT * FROM demand_batches WHERE agent_task_id=?', (self.task['id'],))), 2)

    def test_terminal_legacy_or_saved_report_never_reopens(self):
        # Build a finished ordinary discovery with failed candidates, bypassing
        # advance only to test eligibility guards before their atomic checkpoint.
        dr._discovery(self.store, self.cycle, self.cli); self.refresh()
        with self.store.db:
            self.store.db.execute("UPDATE demand_candidates SET state='failed',error='access_denied'")
            self.store.db.execute("UPDATE demand_batches SET state='done'")
        for guard in ('terminal', 'legacy', 'report'):
            with self.subTest(guard=guard), self.store.db:
                snapshot = copy.deepcopy(self.cycle)
                if guard == 'terminal':
                    self.store.db.execute("UPDATE demand_cycles SET state='completed'")
                elif guard == 'legacy':
                    plan = json.loads(snapshot['plan']); plan['version'] = 2; snapshot['plan'] = dr.dumps(plan)
                else:
                    self.store.event(self.cycle['id'], 'agents', 'role_report', {'task_id': self.task['id'], 'report': {}})
                self.assertFalse(rescue.schedule(self.store, snapshot, self.task, 12, 6, 72))
                self.assertEqual(self.marker(), [])
                self.store.db.execute("UPDATE demand_cycles SET state='running'")

    def test_closed_host_exclusion_is_exact_and_not_vpn_or_parent_domain_rule(self):
        self.original(fail='access_challenge')
        urls = ['https://closed.test/new', 'https://community.closed.test/topic', 'https://another.test/topic']
        self.cli.discovery['candidates'] = [
            {'url': url, 'purpose': 'support', 'source_type': 'discussion'} for url in urls]
        dr._discovery(self.store, self.cycle, self.cli)
        self.assertEqual([r['url'] for r in self.store.rows('SELECT url FROM demand_candidates WHERE batch_index=1 ORDER BY rowid')], urls[1:])

    def test_foreign_rescue_sources_reach_analysis_role_report_and_export(self):
        self.tearDown_for_subcase(foreign_first=True)
        self.original()
        original = self.store.rows('SELECT * FROM demand_batches WHERE batch_index=0')[0]
        self.cli.discovery['candidates'] = [
            {'url': f'https://open.test/post/{i}', 'purpose': 'support', 'source_type': 'discussion'} for i in range(2)]
        dr._discovery(self.store, self.cycle, self.cli); self.refresh()
        dr._fetch(self.store, self.cycle, lambda url: {
            'ok': True, 'url': url, 'text': 'A public first-person discussion. ' + deep_quote(int(url.rsplit('/', 1)[1]))})
        self.refresh()
        model = AgentCli()
        dr._analyze(self.store, self.cycle, model); self.refresh()
        self.assertEqual(self.cycle['stage'], 'agent_report')
        observations = dr._agent_observations(self.store, self.cycle['id'], self.task['id'])
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(o['geography'] == 'international' and o['kind'] == 'foreign_context' for o in observations))
        self.assertEqual(len(dr._agent_sources(self.store, self.cycle['id'], self.task['id'])), 2)
        dr._agent_report(self.store, self.cycle, model); self.refresh()
        report = dr._agent_events(self.store, self.cycle['id'], 'role_report', self.task['id'])[0]
        self.assertEqual(len(report['source_ids']), 2)
        self.assertTrue(report['report']['findings'])
        self.cards.mkdir(exist_ok=True)
        dr._export_agent_report(self.store, self.cycle)
        exported = (self.cards / ('research-cycle-' + self.cycle['id'] + '.md')).read_text(encoding='utf-8')
        self.assertIn(observations[0]['id'], exported)
        self.assertIn(observations[0]['quote'], exported)
        self.assertIn('international', exported)
        coverage = self.store.coverage(self.cycle['id'])
        self.assertEqual(coverage['read'], 2)
        self.assertEqual(coverage['analyzed'], 2)
        self.assertEqual(self.store.rows('SELECT * FROM demand_batches WHERE batch_index=0')[0], original)

    def test_child_rescue_preserves_foreign_ancestry_and_six_url_total(self):
        foreign = self.store.rows("SELECT id FROM demand_agent_tasks WHERE role='foreign'")[0]['id']
        with self.store.db:
            self.store.db.execute('UPDATE demand_agent_tasks SET parent_task_id=?,depth=1 WHERE id=?',
                                  (foreign, self.task['id']))
        self.task = agents.task(self.store.db, self.task['id'])
        self.original(count=4)
        self.assertEqual(self.marker()[0]['remaining_urls'], 2)
        self.cli.discovery['candidates'] = [
            {'url': f'https://open.test/post/{i}', 'purpose': 'support', 'source_type': 'discussion'} for i in range(5)]
        dr._discovery(self.store, self.cycle, self.cli)
        self.assertEqual(rescue.task_url_count(self.store, self.cycle['id'], self.task['id']), 6)
        self.assertEqual({r['geography'] for r in self.store.rows('SELECT geography FROM demand_candidates WHERE batch_index=1')}, {'international'})
        self.assertIn('наследование от родителя', self.cli.calls[-1])


if __name__ == '__main__':
    unittest.main()
