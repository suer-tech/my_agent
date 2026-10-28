"""Real research state-machine integration with deterministic model/HTTP doubles."""
import copy
from datetime import datetime, timedelta, timezone
import json
import re
import unittest
from unittest.mock import patch

from iva_integration import demand_agents as agents
from iva_integration import demand_coverage_runtime as cr
from iva_integration import demand_research as dr
from iva_integration import demand_retention as retention
from iva_integration import scheduled_tasks
from tests.test_demand_coverage_runtime import CoverageFixture, PRICE, EXPERIENCE_A, EXPERIENCE_B, missing
from tests.test_demand_research import AgentCli


class CoverageCli(AgentCli):
    def __init__(self, pricing_first=True, fail_coverage=False):
        super().__init__(child_request=False)
        self.pricing_first = pricing_first
        self.fail_coverage = fail_coverage
        self.coverage_calls = 0
        self.coverage_prompts = []

    def structured(self, prompt, schema):
        properties = schema['properties']
        if 'criteria' in properties:
            self.coverage_calls += 1
            self.coverage_prompts.append(prompt)
            if self.fail_coverage:
                raise RuntimeError('isolated coverage model timeout')
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            sources = payload['sources']
            rows = []
            for required in payload['requirements']:
                key = required['key']
                chosen = [s for s in sources if '/user-' in s['url']] if key in ('user_experience', 'workaround') else [s for s in sources if '/pricing' in s['url']]
                witnesses = []
                for source in chosen[:2]:
                    quote = EXPERIENCE_A if '/user-a' in source['url'] else EXPERIENCE_B if '/user-b' in source['url'] else PRICE
                    witnesses.append({'source_id': source['source_id'], 'quote': quote, 'origin': source['url'],
                                      'kind': 'user_experience' if '/user-' in source['url'] else 'official_solution',
                                      'explanation': 'Источник напрямую описывает проверяемый рабочий процесс и ограничения.'})
                rows.append({'key': key, 'status': 'answered' if witnesses else 'missing',
                             'reason': 'Проверены первичные тексты о выбранном рабочем процессе и его ограничениях.', 'witnesses': witnesses})
            return {'criteria': rows,
                    'queries': ['inventory reservations user experience forum', 'warehouse connector manual reconciliation reviews'],
                    'limitations': ['Открытые сообщения не подтверждают реальную платёжную конверсию.']}
        if 'candidates' in properties:
            self.prompts.append(prompt)
            batch = int(re.search(r'Партия источников №(\d+)', prompt).group(1)) - 1
            urls = [f'https://vendor-{batch}.test/pricing']
            if not self.pricing_first or self.discovery_calls:
                urls += [f'https://forum-{batch}.test/user-a', f'https://community-{batch}.test/user-b']
            self.discovery_calls += 1
            self.last_web_events = [{'type': 'web_search', 'query': 'actual bounded lookup', 'results': [{'url': url} for url in urls]}]
            return {'queries': ['inventory actual user experience', 'stock reservation independent review'],
                    'candidates': [{'url': url, 'purpose': 'support', 'source_type': 'review'} for url in urls], 'limitations': []}
        if 'hypotheses' in properties:
            self.prompts.append(prompt)
            return {'hypotheses': [], 'gaps': [], 'followups': [],
                    'branch_answer': {'verdict': 'inconclusive', 'basis': 'Нет заданной гипотезы для подтверждения.', 'source_ids': [], 'evidence': []}}
        return super().structured(prompt, schema)


def public_page(url):
    quote = EXPERIENCE_A if '/user-a' in url else EXPERIENCE_B if '/user-b' in url else PRICE
    return {'ok': True, 'url': url, 'text': quote + ' Additional public context about inventory synchronization and reservation settings.'}


class CoverageIntegration(CoverageFixture, unittest.TestCase):
    def step(self, cli):
        value = dr.run_step(self.cycle['job_id'], 'Исследовать только программные рабочие процессы.',
                            self.path, self.cards, cli=cli, fetcher=public_page, cycle_id=self.cycle['id'])
        self.refresh()
        return value

    def until(self, cli, stage, limit=70):
        states = []
        for _ in range(limit):
            value = self.step(cli); states.append(value)
            if value['stage'] == stage:
                return states
        self.fail('Checkpoint not reached: ' + str(states[-1]))

    def test_pricing_then_reviews_uses_real_stages_and_preserves_raw_report(self):
        cli = CoverageCli()
        states = self.until(cli, 'agent_review')
        self.assertTrue(any(v['stage'] == 'agent_coverage' for v in states))
        self.assertEqual(cli.coverage_calls, 2)
        self.assertEqual(cli.discovery_calls, 2)
        self.assertEqual(len(self.markers(cr.SEARCH)), 1)
        closed = cr.closed(self.store, self.cycle['id'], self.task['id'])
        self.assertEqual(closed['status'], 'sufficient')
        self.assertIn('user_experience', [m['key'] for m in self.markers(cr.ASSESSED)[0]['assessment']['missing']])
        self.assertFalse(closed['assessment']['missing'])
        reports = dr._agent_events(self.store, self.cycle['id'], 'role_report', self.task['id'])
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['report']['child_requests'], [])
        self.step(cli)
        saved = agents.task(self.store.db, self.task['id'])
        self.assertEqual(saved['status'], 'done')
        self.assertEqual(saved['result']['evidence_coverage'], closed)
        self.assertEqual(saved['result']['report'], reports[0]['report'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM demand_agent_tasks').fetchone()[0], 4)

    def test_restart_after_assessment_does_not_repeat_model_call(self):
        cli = CoverageCli()
        self.until(cli, 'agent_coverage')
        self.step(cli)
        self.assertEqual(cli.coverage_calls, 1)
        self.assertEqual(self.cycle['stage'], 'discovery')
        self.store.close(); self.store = dr.Research(self.path, self.cards)
        self.refresh()
        self.step(cli)
        self.assertEqual(cli.coverage_calls, 1)
        self.assertEqual(cli.discovery_calls, 2)
        self.until(cli, 'agent_review')
        self.assertEqual(cli.coverage_calls, 2)
        self.assertEqual(len(self.markers(cr.SEARCH)), 1)

    def test_assessor_failure_keeps_sources_and_continues_report_with_unavailable(self):
        cli = CoverageCli(fail_coverage=True)
        self.until(cli, 'agent_coverage')
        sources = self.store.rows('SELECT * FROM demand_sources')
        for _ in range(3):
            self.step(cli)
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(self.store.rows('SELECT * FROM demand_sources'), sources)
        closed = cr.closed(self.store, self.cycle['id'], self.task['id'])
        self.assertEqual(closed['status'], 'unavailable')
        self.assertEqual(closed['stop_reason'], 'assessment_model_timeout')
        self.step(cli); self.step(cli)
        saved = agents.task(self.store.db, self.task['id'])
        self.assertEqual(saved['status'], 'done')
        self.assertTrue(saved['result']['report']['summary'])
        self.assertEqual(saved['result']['evidence_coverage']['status'], 'unavailable')

    def test_coverage_prompt_is_role_local_even_with_other_role_source_in_database(self):
        cli = CoverageCli()
        self.until(cli, 'agent_coverage')
        sentinel = 'OTHER_ROLE_SECRET_CONCLUSION_NOT_AN_EVIDENCE_SOURCE'
        with self.store.db:
            self.store.db.execute('INSERT INTO demand_sources VALUES(?,?,?,?,?,?,?)', ('other-role', 'https://elsewhere.test/source', sentinel, sentinel, 'differenthash', dr.now(), None))
        self.step(cli)
        self.assertNotIn(sentinel, cli.coverage_prompts[0])
        self.assertIn(self.task['question'], cli.coverage_prompts[0])
        self.assertIn(PRICE, cli.coverage_prompts[0])

    def test_complete_tree_runs_coverage_for_all_roles_and_exports_durable_coverage(self):
        cli = CoverageCli(pricing_first=False)
        final = None
        for _ in range(110):
            final = self.step(cli)
            if not final['has_more']:
                break
        self.assertFalse(final['has_more'], final)
        self.assertEqual(cli.coverage_calls, 4)
        tasks = agents.snapshot(self.store.db, self.cycle['id'])['tasks']
        self.assertTrue(all(task['status'] == 'done' for task in tasks))
        self.assertTrue(final['coverage']['evidence_coverage']['complete'])
        self.assertEqual(cli.discovery_calls, 4)
        report = (self.cards / ('research-cycle-' + self.cycle['id'] + '.md')).read_text(encoding='utf-8')
        self.assertEqual(report.count('Достаточность свидетельств: sufficient'), 4)
        self.assertFalse(final['notify'])

    def test_unclosed_evidence_question_is_final_gap_and_blocks_otherwise_met_desk_gate(self):
        self.initial(); self.assess()
        self.discover([]); self.assess()
        coverage = self.store.coverage(self.cycle['id'])
        final = dr._final_coverage(self.store, self.cycle, coverage)
        self.assertTrue(any(self.task['id'] in gap and 'Недостаточно свидетельств' in gap for gap in final['analysis_gaps']))
        evidence = [{'kind': 'problem', 'source_type': 'request'}, {'kind': 'problem', 'source_type': 'review'}, {'kind': 'alternative', 'source_type': 'offer'}]
        favorable = {**coverage, 'unique_read_pages': 30, 'unique_analyzed_pages': 30, 'counter_search_completed': True,
                     'agent_progress': {'review_needed': []}}
        with patch.object(self.store, 'evidence', return_value=evidence), patch.object(dr, '_independent', return_value=3), patch.object(dr, '_agent_events', return_value=[{'result': {'decisions': [{'hypothesis_id': 'hyp', 'disposition': 'investigate'}]}}]):
            self.assertFalse(dr._quality(self.store, self.cycle, 'hyp', favorable)['desk_gate_passed'])
            complete = {**favorable, 'evidence_coverage': {'complete': True}}
            self.assertTrue(dr._quality(self.store, self.cycle, 'hyp', complete)['desk_gate_passed'])

    def test_semantic_gap_is_not_service_failure_but_assessor_unavailability_is(self):
        base = {'ok': True, 'has_more': False, 'status': 'completed_with_gaps',
                'coverage': {'evidence_coverage': {'complete': False, 'tasks': [
                    {'task_id': self.task['id'], 'status': 'deferred', 'stop_reason': 'url_budget_exhausted'}]}}}
        self.assertIsNone(scheduled_tasks._research_technical_issue(base))
        self.assertTrue(scheduled_tasks._research_completed_without_technical_failure(base))
        failed = copy.deepcopy(base)
        failed['coverage']['evidence_coverage']['tasks'][0].update(status='unavailable', stop_reason='assessment_model_timeout')
        self.assertEqual(scheduled_tasks._research_technical_issue(failed),
                         {'error_category': 'agent_coverage_failed', 'stage': 'agent_coverage'})
        self.assertFalse(scheduled_tasks._research_completed_without_technical_failure(failed))

    def test_zero_readable_sources_use_deterministic_deficits_without_assessment_calls(self):
        cli = CoverageCli()
        for _ in range(70):
            result = dr.run_step(self.cycle['job_id'], 'Исследовать только программные рабочие процессы.',
                                 self.path, self.cards, cli=cli,
                                 fetcher=lambda url: {'ok': False, 'error_category': 'access_denied'},
                                 cycle_id=self.cycle['id'])
            self.refresh()
            if result['stage'] == 'agent_report':
                break
        self.assertEqual(self.cycle['stage'], 'agent_report')
        self.assertEqual(cli.coverage_calls, 0)
        self.assertEqual(cli.discovery_calls, 3)
        value = cr.closed(self.store, self.cycle['id'], self.task['id'])
        self.assertEqual(value['status'], 'deferred')
        self.assertEqual(value['attempts'], 2)
        self.assertFalse(value['assessment']['sufficient'])
        self.assertFalse(dr._agent_sources(self.store, self.cycle['id'], self.task['id']))

    def test_assessment_uses_medium_floor_without_mutating_shared_profile(self):
        cli = CoverageCli()
        self.until(cli, 'agent_coverage')
        cli.reasoning_effort = 'low'
        self.step(cli)
        self.assertEqual(cli.minimum_reasoning_effort, 'medium')
        self.assertEqual(cli.reasoning_effort, 'medium')
        self.assertEqual(cli.model, 'configured-model')
        self.assertEqual(cli.service_tier, 'fast')

    def test_deferred_questions_survive_compaction_and_only_same_scope_can_reuse_them(self):
        class PricingOnly(CoverageCli):
            def structured(self, prompt, schema):
                value = super().structured(prompt, schema)
                if 'candidates' in value:
                    value['candidates'] = [item for item in value['candidates'] if '/pricing' in item['url']]
                return value
        cli = PricingOnly()
        for _ in range(160):
            final = self.step(cli)
            if not final['has_more']:
                break
        self.assertFalse(final['has_more'])
        same_scope = {**self.cycle, 'id': 'future-same-scope'}
        before = cr.prior_gaps(self.store, same_scope)
        self.assertEqual(len(before), 3)
        wrong_scope = copy.deepcopy(same_scope)
        plan = json.loads(wrong_scope['plan']); plan['scope']['id'] = 'different-business-direction'
        wrong_scope['plan'] = dr.dumps(plan)
        self.assertEqual(cr.prior_gaps(self.store, wrong_scope), [])
        other_owner_job = {**same_scope, 'job_id': 'unrelated-job'}
        self.assertEqual(cr.prior_gaps(self.store, other_owner_job), [])
        frozen = retention.compact(self.store, clock=datetime.now(timezone.utc) + timedelta(days=20), dry_run=False)
        self.assertEqual(frozen['cycles_compacted'], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM demand_agent_tasks').fetchone()[0], 0)
        self.assertEqual(cr.prior_gaps(self.store, same_scope), before)
        view = dr._reported_cycle(self.store, self.cycle)
        self.assertFalse(view['coverage']['evidence_coverage']['complete'])
        self.assertEqual(len([task for task in view['orchestration']['tasks']
                              if task['result']['evidence_coverage']['status'] == 'deferred']), 3)


if __name__ == '__main__':
    unittest.main()
