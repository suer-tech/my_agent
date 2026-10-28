"""Research-direction guards against the real software/physical-sales mix-up."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import demand_research as research
from iva_integration import demand_scope as scope
from tests.test_demand_agents import research_plan, assignment, child
from tests.test_demand_research import AgentCli, canonical


TOPIC = 'ПО: небольшие ремонтные компании; расписание мастеров, заявки и сметы'
OWNER = 'OWNER_BROAD_GOAL_912: изучать ПО, а также продавать хозблоки и септики через MAX.'


class AuditCli:
    def __init__(self, plan=None, verdicts=None):
        self.plan = plan or research_plan()
        self.verdicts = verdicts or {}
        self.prompts = []
        self.audit_calls = 0

    def structured(self, prompt, schema):
        self.prompts.append(prompt)
        if 'researchers' in schema['properties']:
            return copy.deepcopy(self.plan)
        self.audit_calls += 1
        keys = schema['properties']['scope_decisions']['items']['properties']['key']['enum']
        return {'scope_id': schema['properties']['scope_id']['enum'][0],
                'scope_decisions': [{'key': key, 'relationship': self.verdicts.get(key, 'same_problem'),
                                     'reason': 'Проверено направление, цель поручения и связь с текущим рабочим процессом.'}
                                    for key in keys]}


class ScopeGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'research.sqlite3'
        self.store = research.Research(self.path, Path(self.temp.name) / 'cards')
        self.addCleanup(self.store.close)
        with patch.object(research, 'TRACKS', (('software', TOPIC),)), patch.object(research, 'AGENT_RESEARCH_ENABLED', True):
            self.cycle = self.store.cycle('scope-job', OWNER)

    def update_plan(self, **changes):
        plan = json.loads(self.cycle['plan'])
        plan.update(changes)
        plan['scope'] = scope.contract(self.cycle['track'], self.cycle['topic'], question=plan.get('question'),
                                       revisit=plan.get('revisit'), open_exploration=plan.get('open_exploration'))
        self.cycle['plan'] = json.dumps(plan, ensure_ascii=False)
        with self.store.db:
            self.store.db.execute('UPDATE demand_cycles SET plan=? WHERE id=?', (self.cycle['plan'], self.cycle['id']))

    def hypothesis(self, identity, title):
        payload = {'title': title, 'buyer': 'Пользователь программы', 'problem': title,
                   'unknowns': [], 'next_test': {'query': 'Проверить этот прежний критерий'}}
        with self.store.db:
            self.store.db.execute('''INSERT INTO demand_hypotheses
                (id,job_id,stable_key,track,title,state,payload,created,updated,last_checked,revision,signature)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identity, 'scope-job', identity, 'software', title, 'signal', json.dumps(payload, ensure_ascii=False),
                 '2026-01-01', '2026-01-01', '2026-01-01', 1, identity))

    def test_business_direction_is_bound_not_a_product_keyword_blacklist(self):
        self.assertEqual(scope.lane('software'), 'software')
        self.assertEqual(scope.lane('hozbloki'), scope.lane('b2c'))
        brief = scope.brief(self.cycle)
        self.assertEqual(brief['starting_focus'], TOPIC)
        self.assertNotIn(OWNER, json.dumps(brief, ensure_ascii=False))
        self.assertIn('смежной ниши', brief['pivot_rule'])
        self.assertIn('предмет учёта', brief['boundary'])

    def test_saved_contract_rejects_swapping_cycle_direction_or_topic(self):
        for change in ({'track': 'b2c'}, {'topic': 'Продажа септиков через MAX'}):
            with self.subTest(change=change), self.assertRaisesRegex(scope.ScopeError, 'contract_changed'):
                scope.brief({**self.cycle, **change})

    def test_bad_root_plan_never_starts_search_and_feedback_reaches_retry(self):
        mixed = research_plan()
        mixed['researchers'][0].update(question='Есть ли спрос на покупку хозблоков с доставкой?',
                                      queries=['хозблок купить доставка отзыв', 'септик продажа комиссия MAX'])
        bad = AuditCli(mixed, {'buyer_problem': 'cross_lane'})
        with self.assertRaisesRegex(scope.ScopeError, 'plan_rejected'):
            research._agent_plan(self.store, self.cycle, bad)
        for table in ('demand_agent_plans', 'demand_agent_tasks', 'demand_candidates', 'demand_batches'):
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        good = AuditCli()
        result = research._agent_plan(self.store, self.cycle, good)
        self.assertEqual(result['stage'], 'discovery')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM demand_agent_tasks').fetchone()[0], 4)
        self.assertIn('Предыдущий план отклонён до поиска', good.prompts[0])
        self.assertIn('cross_lane', good.prompts[0])

    def test_overall_business_decision_is_checked_as_well_as_each_role(self):
        cli = AuditCli(verdicts={'@cycle_decision': 'cross_lane'})
        with self.assertRaisesRegex(scope.ScopeError, 'plan_rejected'):
            research._agent_plan(self.store, self.cycle, cli)
        self.assertFalse(self.store.rows('SELECT * FROM demand_agent_tasks'))

    def test_invalid_semantic_review_cannot_admit_plan(self):
        class WrongReview(AuditCli):
            def structured(self, prompt, schema):
                result = super().structured(prompt, schema)
                if 'scope_decisions' in result:
                    result['scope_id'] = 'another-cycle'
                return result
        with self.assertRaisesRegex(scope.ScopeError, 'review_invalid'):
            research._agent_plan(self.store, self.cycle, WrongReview())
        self.assertFalse(self.store.rows('SELECT * FROM demand_agent_plans'))

    def test_duplicate_review_keys_fail_closed(self):
        class Duplicate(AuditCli):
            def structured(self, prompt, schema):
                value = super().structured(prompt, schema)
                if 'scope_decisions' in value:
                    value['scope_decisions'][-1] = value['scope_decisions'][0]
                return value
        with self.assertRaisesRegex(scope.ScopeError, 'review_invalid'):
            research._agent_plan(self.store, self.cycle, Duplicate())
        self.assertFalse(self.store.rows('SELECT * FROM demand_agent_tasks'))

    def test_fresh_topic_does_not_receive_broad_owner_goal_or_unrelated_old_ideas(self):
        self.hypothesis('old-accounting', 'UNRELATED_ACCOUNTING_313')
        cli = AuditCli()
        research._agent_plan(self.store, self.cycle, cli)
        self.assertTrue(all('OWNER_BROAD_GOAL_912' not in prompt for prompt in cli.prompts))
        self.assertTrue(all('UNRELATED_ACCOUNTING_313' not in prompt for prompt in cli.prompts))
        self.assertIn(TOPIC, cli.prompts[0])
        self.assertIn('Хабр', cli.prompts[0])
        plan = json.loads(self.store.rows('SELECT plan FROM demand_cycles')[0]['plan'])
        self.assertEqual(plan['prior_context'], [])

    def test_revisit_uses_only_explicit_target_even_when_it_is_older_than_six(self):
        self.hypothesis('target', 'EXPLICIT_TARGET_171')
        for index in range(9):
            self.hypothesis('other-' + str(index), 'UNRELATED_OLD_IDEA_' + str(index))
        self.update_plan(revisit={'id': 'target', 'title': 'EXPLICIT_TARGET_171',
                                  'test': {'query': 'ORIGINAL_TEST_CRITERION_25'}})
        cli = AuditCli()
        research._agent_plan(self.store, self.cycle, cli)
        self.assertIn('EXPLICIT_TARGET_171', cli.prompts[0])
        self.assertIn('ORIGINAL_TEST_CRITERION_25', cli.prompts[0])
        self.assertNotIn('UNRELATED_OLD_IDEA_', cli.prompts[0])
        plan = json.loads(self.store.rows('SELECT plan FROM demand_cycles')[0]['plan'])
        self.assertEqual([item['id'] for item in plan['prior_context']], ['target'])

    def test_question_preserves_original_criteria_without_importing_other_questions(self):
        self.update_plan(question={'id': 'q1', 'hypothesis_id': 'h1', 'question': 'Сколько ручных переносов?',
                                   'support_condition': 'Три независимые истории ручного переноса',
                                   'refute_condition': 'Существующая функция закрывает проблему'})
        self.assertEqual(scope.prior_ids(self.cycle), {'h1'})
        brief = scope.brief(self.cycle)
        self.assertEqual(brief['question']['support_condition'], 'Три независимые истории ручного переноса')
        plan = json.loads(self.cycle['plan'])
        plan['question']['support_condition'] = 'Достаточно одного объявления поставщика'
        with self.assertRaisesRegex(scope.ScopeError, 'contract_changed'):
            scope.brief({**self.cycle, 'plan': plan})

    def test_worker_classifies_rejected_direction_and_preserves_retry_checkpoint(self):
        cli = AuditCli(verdicts={'buyer_problem': 'cross_lane'})
        result = research.run_step('scope-job', OWNER, db_path=self.path,
                                   cards_dir=Path(self.temp.name) / 'cards', cli=cli,
                                   cycle_id=self.cycle['id'])
        self.assertEqual(result['error_category'], 'research_scope_plan_rejected')
        self.assertTrue(result['has_more'])
        cycle = self.store.rows('SELECT state,stage FROM demand_cycles')[0]
        self.assertEqual(cycle, {'state': 'running', 'stage': 'agent_plan'})
        self.assertFalse(self.store.rows('SELECT * FROM demand_agent_tasks'))

    def test_child_crossing_direction_is_removed_without_changing_parent_state(self):
        parent = {'id': 'parent', **assignment('buyer_problem')}
        requests = [child('software-child'), child('sell-goods')]
        cli = AuditCli(verdicts={'sell-goods': 'cross_lane'})
        before = list(self.store.db.iterdump())
        result = scope.guard_children(self.store, self.cycle, parent, requests, cli)
        self.assertEqual([item['key'] for item in result['requests']], ['software-child'])
        self.assertEqual(result['limitations'][0]['reason'], 'research_scope_cross_lane')
        self.assertFalse(self.store.rows('SELECT * FROM demand_agent_tasks'))
        self.assertEqual(self.store.rows('SELECT state FROM demand_cycles')[0]['state'], 'running')
        self.assertNotEqual(before, list(self.store.db.iterdump()))  # only the audit event is saved
        repeated = scope.guard_children(self.store, self.cycle, parent, requests, cli)
        self.assertEqual(repeated, result)
        self.assertEqual(cli.audit_calls, 1)
        changed = copy.deepcopy(requests)
        changed[0]['question'] += ' Уточнение той же программной задачи.'
        scope.guard_children(self.store, self.cycle, parent, changed, cli)
        self.assertEqual(cli.audit_calls, 2)

    def test_connected_software_pivot_with_physical_product_name_remains_allowed(self):
        parent = {'id': 'parent', **assignment('buyer_problem')}
        proposal = child('warehouse')
        proposal.update(question='Нужно ли продавцам хозблоков ПО для синхронизации остатков?',
                        why_needed='Тот же процесс учёта заказов из источника влияет на остатки продавца хозблоков.')
        cli = AuditCli(verdicts={'warehouse': 'connected_pivot'})
        result = scope.guard_children(self.store, self.cycle, parent, [proposal], cli)
        self.assertEqual(result['requests'], [proposal])
        self.assertEqual(result['limitations'], [])
        self.assertIn('по цели, а не по отдельному слову', cli.prompts[0])

    def test_unclear_child_cannot_silently_spend_search_budget(self):
        result = scope.guard_children(self.store, self.cycle, {'id': 'parent'}, [child('unclear')],
                                      AuditCli(verdicts={'unclear': 'uncertain'}))
        self.assertFalse(result['requests'])
        self.assertEqual(result['limitations'][0]['reason'], 'research_scope_uncertain')

    def test_analysis_does_not_promote_an_opposite_direction_hypothesis(self):
        proposals = [{'key': 'software', 'buyer': 'Продавец', 'problem': 'Учёт остатков', 'offer': 'Программа'},
                     {'key': 'goods', 'buyer': 'Дачник', 'problem': 'Где купить хозблок', 'offer': 'Агентская продажа'}]
        allowed, gaps = scope.guard_hypotheses(self.store, self.cycle, proposals,
                                               AuditCli(verdicts={'1': 'cross_lane'}))
        self.assertEqual(allowed, proposals[:1])
        self.assertEqual(gaps[0]['relationship'], 'cross_lane')

    def test_legacy_anchored_cycle_keeps_format_without_unrequested_scope_audits(self):
        plan = json.loads(self.cycle['plan']); plan.pop('scope')
        legacy = {**self.cycle, 'plan': json.dumps(plan)}
        cli = AuditCli()
        scope.validate_plan(self.store, legacy, research_plan(), cli)
        result = scope.guard_children(self.store, legacy, {'id': 'parent'}, [child()], cli)
        self.assertEqual(result['requests'], [child()])
        self.assertEqual(cli.audit_calls, 0)

    def test_actual_discovery_prompt_is_scoped_and_includes_habr_methodology(self):
        cli = AgentCli(child_request=False)
        research._agent_plan(self.store, self.cycle, cli)
        current = self.store.rows('SELECT * FROM demand_cycles')[0]
        with patch.object(research, '_canonical', side_effect=canonical):
            research._discovery(self.store, current, cli)
        prompt = next(prompt for prompt in cli.prompts if prompt.startswith('Ты выполняешь'))
        self.assertNotIn('OWNER_BROAD_GOAL_912', prompt)
        self.assertIn(TOPIC, prompt)
        self.assertIn('qna.habr.com', prompt)
        self.assertIn('site:habr.com', prompt)
        self.assertIn('Примеры тематических запросов', prompt)

    def test_isolated_native_probe_harness_preserves_original_db(self):
        from deploy.verify_demand_scope import material_digest, readonly_backup, run_cases
        from iva_integration import demand_agents as agents

        mixed = research_plan()
        mixed['researchers'][0]['question'] = 'OLD_MIXED_COMMERCE: есть ли спрос на покупку хозблоков с доставкой?'
        with self.store.db:
            agents.save_plan(self.store.db, self.cycle['id'], self.cycle['job_id'], 'software', mixed)
        before = material_digest(self.store)
        output = Path(self.temp.name) / 'probe'
        output.mkdir()
        snapshot = output / 'snapshot.sqlite3'
        readonly_backup(self.path, snapshot)

        class FakeNative(AuditCli):
            last_execution = {'reasoning_effort': 'medium', 'web_search': 'disabled', 'read_only': True}
            last_web_events = []

            def structured(self, prompt, schema):
                self.verdicts = {'buyer_problem': 'cross_lane'} if 'OLD_MIXED_COMMERCE' in prompt else {}
                if 'sell_goods_commission' in prompt:
                    self.verdicts = {'sell_goods_commission': 'cross_lane'}
                return super().structured(prompt, schema)

        result = run_cases(snapshot, output, FakeNative, self.cycle['id'])
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['native_calls']), 4)
        self.assertEqual(material_digest(self.store), before)


if __name__ == '__main__':
    unittest.main()
