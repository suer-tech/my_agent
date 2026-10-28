"""Durable evidence deficits are bounded collection work, never child evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import demand_agents as agents
from iva_integration import demand_coverage_runtime as cr
from iva_integration import demand_discovery_rescue as rescue
from iva_integration import demand_research as dr
from tests.test_demand_agents import research_plan
from tests.test_demand_research import FakeCli, canonical


PRICE = 'Our software synchronizes warehouse inventory on the professional plan.'
EXPERIENCE_A = 'We still reconcile shop orders manually every evening because warehouse reservations are not synchronized.'
EXPERIENCE_B = 'My staff spend three hours checking stock after each import; the connector skips reserved quantities.'


def missing(task):
    return {'version': 1, 'task_id': task['id'], 'role': task['role'], 'question': task['question'],
            'expected_evidence': task['expected_evidence'], 'criteria': [], 'sufficient': False,
            'missing': [{'key': 'user_experience', 'question': task['question'],
                         'reason': 'Нет независимых рассказов пользователей об исследуемой функции.'}],
            'queries': ['inventory reservations user experience forum', 'warehouse connector manual reconciliation reviews'],
            'limitations': ['Официальный тариф не заменяет опыт пользователя.']}


class CoverageFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'research.sqlite3'
        self.cards = Path(self.tmp.name) / 'cards'
        self.url_patch = patch.object(dr, '_canonical', side_effect=canonical)
        self.url_patch.start(); self.addCleanup(self.url_patch.stop)
        self.enabled_patch = patch.object(dr, 'EVIDENCE_COVERAGE_ENABLED', True)
        self.enabled_patch.start(); self.addCleanup(self.enabled_patch.stop)
        self.store = dr.Research(self.path, self.cards)
        self.addCleanup(lambda: self.store.close())
        self.cycle = self.store.cycle('coverage-test', 'Исследовать только программные рабочие процессы.')
        with self.store.db:
            value = research_plan()
            value['researchers'].sort(key=lambda item: item['role'] != 'foreign')
            agents.save_plan(self.store.db, self.cycle['id'], self.cycle['job_id'], 'software', value)
            self.store.advance(self.cycle)
        self.refresh()
        self.task = agents.next_task(self.store.db, self.cycle['id'])

    def refresh(self):
        self.cycle = self.store.rows('SELECT * FROM demand_cycles WHERE id=?', (self.cycle['id'],))[0]

    def discover(self, urls):
        cli = FakeCli(discovery={'queries': ['inventory actual user experience', 'stock reservation independent review'],
                                 'candidates': [{'url': url, 'purpose': 'support', 'source_type': 'review'} for url in urls],
                                 'limitations': []})
        dr._discovery(self.store, self.cycle, cli); self.refresh()
        return cli

    def finish_batch(self, body=PRICE, fail=None):
        while self.cycle['stage'] == 'fetch':
            dr._fetch(self.store, self.cycle, lambda url: {'ok': False, 'error_category': fail} if fail else
                      {'ok': True, 'url': url, 'text': body + ' Public documentation for the relevant workflow.'})
            self.refresh()
        with self.store.db:
            self.store.db.execute("UPDATE demand_candidates SET analysis_state='done' WHERE state='read'")
            self.store.advance(self.cycle)
        self.refresh()

    def assess(self, value=None):
        with self.store.db:
            result = cr.settle(self.store, self.cycle, self.task, value or missing(self.task))
            self.store.advance(self.cycle)
        self.refresh()
        return result

    def markers(self, event):
        return cr.events(self.store, self.cycle['id'], self.task['id'], event)

    def initial(self, count=1, fail=None):
        self.discover([f'https://vendor.test/prices/{i}' for i in range(count)])
        self.finish_batch(fail=fail)


class CoverageRuntime(CoverageFixture, unittest.TestCase):
    def test_read_pricing_still_schedules_same_assignment_and_preserves_source(self):
        self.initial()
        before = self.store.rows('SELECT * FROM demand_sources')
        self.assertEqual(self.cycle['stage'], 'agent_coverage')
        self.assertTrue(self.assess())
        self.assertEqual(self.cycle['stage'], 'discovery')
        self.assertEqual(self.store.rows('SELECT * FROM demand_sources'), before)
        batches = self.store.rows('SELECT agent_task_id,state FROM demand_batches ORDER BY batch_index')
        self.assertEqual(batches, [{'agent_task_id': self.task['id'], 'state': 'done'},
                                  {'agent_task_id': self.task['id'], 'state': 'pending'}])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM demand_agent_tasks').fetchone()[0], 4)

    def test_restart_pending_search_never_appends_duplicate(self):
        self.initial(); self.assess()
        before = self.store.rows('SELECT * FROM demand_batches')
        marker = self.markers(cr.SEARCH)
        self.store.close(); self.store = dr.Research(self.path, self.cards)
        self.refresh()
        with self.store.db:
            self.assertEqual(self.store.advance(self.cycle), 'discovery')
        self.assertEqual(self.store.rows('SELECT * FROM demand_batches'), before)
        self.assertEqual(self.markers(cr.SEARCH), marker)
        self.assertFalse(cr.closed(self.store, self.cycle['id'], self.task['id']))

    def test_two_targeted_rounds_terminate_with_persisted_question(self):
        self.initial()
        for index in range(2):
            self.assertTrue(self.assess())
            self.discover([f'https://different{index}.test/pricing'])
            self.finish_batch()
        self.assertFalse(self.assess())
        value = cr.closed(self.store, self.cycle['id'], self.task['id'])
        self.assertEqual(value['stop_reason'], 'search_rounds_exhausted')
        self.assertEqual(value['attempts'], 2)
        self.assertEqual(value['question'], self.task['question'])
        self.assertEqual(len(value['searches']), 2)
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(len(self.markers(cr.ASSESSED)), 3)

    def test_existing_zero_source_rescue_counts_toward_two_search_limit(self):
        self.initial()
        with self.store.db:
            self.store.event(self.cycle['id'], 'agents', rescue.EVENT, {'task_id': self.task['id'], 'batch_index': 0})
        self.assertTrue(self.assess())
        self.assertEqual(self.markers(cr.SEARCH)[0]['attempt'], 2)
        self.discover(['https://another.test/pricing']); self.finish_batch()
        self.assertFalse(self.assess())
        self.assertEqual(cr.closed(self.store, self.cycle['id'], self.task['id'])['stop_reason'], 'search_rounds_exhausted')

    def test_initial_search_reserves_four_of_twelve_urls(self):
        self.initial(count=20)
        self.assertEqual(len(cr.candidates(self.store, self.cycle['id'], self.task['id'])), 8)
        self.assess()
        self.assertEqual(self.markers(cr.SEARCH)[0]['remaining_urls'], 4)
        self.assertEqual(self.markers(cr.SEARCH)[0]['candidate_limit'], 2)
        self.discover([f'https://next.test/{i}' for i in range(20)]); self.finish_batch()
        self.assess()
        self.discover([f'https://last.test/{i}' for i in range(20)]); self.finish_batch()
        self.assess()
        self.assertEqual(len(cr.candidates(self.store, self.cycle['id'], self.task['id'])), 12)
        self.assertEqual(cr.closed(self.store, self.cycle['id'], self.task['id'])['stop_reason'], 'url_budget_exhausted')

    def test_child_reserves_two_urls_and_never_exceeds_six(self):
        with self.store.db:
            parent = self.store.rows("SELECT id FROM demand_agent_tasks WHERE id<>? LIMIT 1", (self.task['id'],))[0]['id']
            self.store.db.execute('UPDATE demand_agent_tasks SET parent_task_id=?,depth=1 WHERE id=?', (parent, self.task['id']))
        self.task = agents.task(self.store.db, self.task['id'])
        self.initial(count=20)
        self.assertEqual(len(cr.candidates(self.store, self.cycle['id'], self.task['id'])), 4)
        for index in range(2):
            self.assess()
            self.assertEqual(self.markers(cr.SEARCH)[-1]['candidate_limit'], 1)
            self.discover([f'https://child{index}.test/{i}' for i in range(8)]); self.finish_batch()
        self.assertFalse(self.assess())
        self.assertEqual(len(cr.candidates(self.store, self.cycle['id'], self.task['id'])), 6)

    def test_cycle_budget_exhaustion_has_no_search(self):
        self.initial()
        value = json.loads(self.cycle['plan']); value['max_urls'] = 1
        with self.store.db:
            self.store.db.execute('UPDATE demand_cycles SET plan=? WHERE id=?', (dr.dumps(value), self.cycle['id']))
        self.refresh()
        self.assertFalse(self.assess())
        self.assertFalse(self.markers(cr.SEARCH))
        self.assertEqual(cr.closed(self.store, self.cycle['id'], self.task['id'])['stop_reason'], 'url_budget_exhausted')

    def test_closed_hosts_and_previous_urls_are_excluded_without_spending_allowance(self):
        self.initial(fail='access_denied'); self.assess()
        cli = self.discover(['https://vendor.test/new', 'https://vendor.test/prices/0', 'https://public.test/review'])
        rows = self.store.rows('SELECT url FROM demand_candidates WHERE batch_index=1')
        self.assertEqual(rows, [{'url': 'https://public.test/review'}])
        self.assertIn('Нет независимых рассказов', cli.calls[0])
        self.assertIn('warehouse connector manual reconciliation reviews', cli.calls[0])
        self.assertIn('vendor.test', cli.calls[0])

    def test_no_new_urls_stops_instead_of_third_query(self):
        self.initial(); self.assess()
        self.discover([])
        self.assertFalse(self.assess())
        self.assertEqual(cr.closed(self.store, self.cycle['id'], self.task['id'])['stop_reason'], 'no_new_sources')
        self.assertEqual(len(self.markers(cr.SEARCH)), 1)

    def test_sufficient_answer_closes_without_extra_collection(self):
        self.initial()
        value = missing(self.task); value.update(sufficient=True, missing=[])
        self.assertFalse(self.assess(value))
        self.assertEqual(cr.closed(self.store, self.cycle['id'], self.task['id'])['status'], 'sufficient')
        self.assertEqual(self.cycle['stage'], 'agent_report')

    def test_technical_failed_batch_does_not_claim_sufficient_or_restart_discovery(self):
        self.initial()
        with self.store.db:
            self.store.db.execute("UPDATE demand_batches SET state='failed',error='schema_invalid'")
        self.refresh()
        value = missing(self.task); value.update(sufficient=True, missing=[])
        self.assertFalse(self.assess(value))
        self.assertEqual(cr.closed(self.store, self.cycle['id'], self.task['id'])['stop_reason'], 'collection_technical_failure')
        self.assertEqual(self.cycle['stage'], 'agent_report')

    def test_legacy_cycle_keeps_original_zero_source_rescue(self):
        value = json.loads(self.cycle['plan']); value.pop('evidence_coverage_version')
        with self.store.db:
            self.store.db.execute('UPDATE demand_cycles SET plan=? WHERE id=?', (dr.dumps(value), self.cycle['id']))
        self.refresh(); self.initial(fail='access_denied')
        self.assertEqual(self.cycle['stage'], 'discovery')
        self.assertEqual(len(self.markers(rescue.EVENT)), 1)
        self.assertFalse(self.markers(cr.SEARCH))

    def test_terminal_cycle_cannot_schedule_new_work(self):
        self.initial()
        with self.store.db:
            self.store.db.execute("UPDATE demand_cycles SET state='completed_with_gaps' WHERE id=?", (self.cycle['id'],))
        self.refresh()
        self.assertFalse(self.assess())
        self.assertFalse(self.markers(cr.SEARCH))


if __name__ == '__main__':
    unittest.main()
