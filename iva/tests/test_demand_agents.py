import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from iva_integration import demand_agents as agents


def assignment(role, key=None):
    key = key or role
    return {'key': key, 'role': role,
            'question': f'Какие конкретные наблюдения для направления {key} изменят выбор решения?',
            'scope': f'Исследовать только направление {key} и публичные рассказы российского малого бизнеса.',
            'methods': 'Сопоставить первичные рассказы с документацией и явно найти противоречащие примеры.',
            'queries': [f'{key} сложности сверки заказов отзывы', f'{key} импорт заказов решено существующим сервисом'],
            'support_condition': 'Независимые рассказы подтверждают существенный нерешённый сбой в указанном рабочем процессе.',
            'refute_condition': 'Проверенные рассказы показывают закрытие предполагаемой проблемы доступной существующей альтернативой.',
            'expected_evidence': 'Дословные фрагменты первичных источников с адресом датой и оговорками применимости.',
            'deliverable': 'Список наблюдений и противоречий с неизвестными величинами без утверждения готовности платить.'}


def research_plan():
    return {'objective': 'Понять остаётся ли существенная проблема сверки заказов у российского малого бизнеса.',
            'decision': 'Решить заслуживает ли эта гипотеза следующей проверки и какие наблюдения опровергают её.',
            'rationale': 'Имеющиеся свидетельства ограничены документацией поэтому нужны рассказы покупателей и проверка альтернатив.',
            'researchers': [assignment(role) for role in agents.ROLES]}


def child(key='integration', role='alternatives'):
    return {**assignment(role, key), 'source_ids': ['s1'],
            'trigger_quote': 'We still reconcile imported orders manually every day.',
            'why_needed': 'Прочитанный источник описывает ручную сверку после импорта поэтому надо проверить ограничение интеграции.'}


class DurableResearchAgents(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'research.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(lambda: self.db.close())
        self.db.executescript('''
            CREATE TABLE demand_cycles(id TEXT,job_id TEXT,track TEXT);
            CREATE TABLE demand_sources(id TEXT,url TEXT,text TEXT);
            CREATE TABLE demand_candidates(cycle_id TEXT,source_id TEXT,state TEXT);
            INSERT INTO demand_cycles VALUES('cycle','job','software'),('other','other-job','software');
            INSERT INTO demand_sources VALUES('s1','https://example.test/story','We still reconcile imported orders manually every day.'),
                ('unread','https://example.test/unread','We still reconcile imported orders manually every day.'),
                ('foreign','https://example.test/other','We still reconcile imported orders manually every day.');
            INSERT INTO demand_candidates VALUES('cycle','s1','read'),('cycle','unread','pending'),('other','foreign','read');
        ''')
        agents.ensure(self.db)
        self.db.commit()

    def setup_plan(self):
        return agents.save_plan(self.db, 'cycle', 'job', 'software', research_plan())

    def begin(self):
        row = agents.next_task(self.db, 'cycle')
        return agents.start_task(self.db, row['id'])

    def finish(self, row, status='done'):
        return agents.finish_task(self.db, row['id'], {'summary': 'Observed source; willingness to pay remains unknown.'},
                                  ['s1'], {'s1'}, status=status)

    def finish_all(self):
        while agents.next_task(self.db, 'cycle'):
            self.finish(self.begin())

    def test_decorative_quote_pair_is_removed_only_after_verbatim_source_check(self):
        self.setup_plan(); parent = self.begin()
        proposal = child('quoted-original')
        original = proposal['trigger_quote']
        proposal['trigger_quote'] = '«' + original + '»'
        with self.db:
            result = agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})
        self.assertEqual(len(result['admitted']), 1)
        saved = agents.task(self.db, result['admitted'][0])
        self.assertEqual(saved['trigger_quote'], original)
        self.assertEqual(proposal['trigger_quote'], '«' + original + '»')
        self.assertEqual(self.db.execute("SELECT count(*) FROM demand_agent_events WHERE event='child_quote_normalized'").fetchone()[0], 1)

    def test_wrapping_a_paraphrase_or_unread_source_does_not_make_it_evidence(self):
        self.setup_plan(); parent = self.begin()
        paraphrase = child('fabricated-quote')
        paraphrase['trigger_quote'] = '«We always reconcile all the imported orders automatically.»'
        unread = child('unread-quote')
        unread['source_ids'] = ['unread']
        unread['trigger_quote'] = '«' + unread['trigger_quote'] + '»'
        with self.db:
            result = agents.enqueue_children(self.db, parent['id'], [paraphrase, unread], {'s1', 'unread'})
        self.assertEqual(result['admitted'], [])
        self.assertEqual([item['reason'] for item in result['rejected']],
                         ['agent_child_quote_not_found', 'agent_child_sources_not_read'])

    def test_validated_plan_and_individual_task_state_survive_restart(self):
        saved = self.setup_plan()
        self.assertEqual(len(saved['tasks']), 4)
        before = self.db.total_changes
        picked = agents.next_task(self.db, 'cycle')
        self.assertEqual(self.db.total_changes, before)
        started = agents.start_task(self.db, picked['id'])
        self.assertEqual(agents.start_task(self.db, picked['id']), started)
        self.db.commit(); self.db.close(); self.db = sqlite3.connect(self.path)
        self.assertEqual(agents.next_task(self.db, 'cycle')['id'], started['id'])
        self.assertEqual(agents.next_task(self.db, 'cycle')['status'], 'running')
        self.assertEqual(agents.save_plan(self.db, 'cycle', 'job', 'software', research_plan())['tasks'][0]['id'], started['id'])
        changed = research_plan(); changed['decision'] += ' Changed later.'
        with self.assertRaisesRegex(ValueError, 'immutable'):
            agents.save_plan(self.db, 'cycle', 'job', 'software', changed)

    def test_empty_generic_duplicate_and_identical_criteria_plans_rejected_before_tasks(self):
        variants = []
        bad = research_plan(); bad['researchers'][0]['methods'] = 'проверить'; variants.append(bad)
        bad = research_plan(); bad['researchers'][1]['question'] = bad['researchers'][0]['question']; variants.append(bad)
        bad = research_plan(); bad['researchers'][0]['refute_condition'] = bad['researchers'][0]['support_condition']; variants.append(bad)
        bad = research_plan(); bad['researchers'][3]['role'] = 'foreign'; variants.append(bad)
        bad = research_plan(); bad['objective'] = 'research ' * 20; variants.append(bad)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                agents.save_plan(self.db, 'cycle', 'job', 'software', value)
        self.assertEqual(agents.snapshot(self.db, 'cycle')['tasks'], [])

    def test_only_one_assigned_task_runs_and_critic_is_after_admitted_children(self):
        saved = self.setup_plan()
        parent = self.begin()
        with self.assertRaisesRegex(ValueError, 'not_next'):
            agents.start_task(self.db, saved['tasks'][1]['id'])
        admitted = agents.enqueue_children(self.db, parent['id'], [child()], {'s1'})['admitted'][0]
        self.finish(parent)
        order = []
        while agents.next_task(self.db, 'cycle'):
            row = self.begin(); order.append(row); self.finish(row)
        self.assertLess(next(i for i, row in enumerate(order) if row['id'] == admitted),
                        next(i for i, row in enumerate(order) if row['role'] == 'critic'))
        self.assertTrue(agents.snapshot(self.db, 'cycle')['ready_for_synthesis'])

    def test_child_requires_actual_read_provenance_and_exact_quote(self):
        self.setup_plan(); parent = self.begin()
        for identity in ('invented', 'unread', 'foreign'):
            proposal = child(); proposal['source_ids'] = [identity]
            result = agents.enqueue_children(self.db, parent['id'], [proposal], {identity})
            self.assertEqual(result['rejected'][0]['reason'], 'agent_child_sources_not_read')
        proposal = child(); proposal['trigger_quote'] = 'A made up quotation which no source actually contains.'
        self.assertEqual(agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})['rejected'][0]['reason'], 'agent_child_quote_not_found')
        proposal = child(); proposal['question'] += ' https://invented.test/new'
        self.assertEqual(agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})['rejected'][0]['reason'], 'agent_child_url_not_in_sources')
        self.assertEqual(len(agents.snapshot(self.db, 'cycle')['tasks']), 4)

    def test_dedup_does_not_mutate_predeclared_tests_or_create_second_child(self):
        self.setup_plan(); parent = self.begin()
        first = agents.enqueue_children(self.db, parent['id'], [child()], {'s1'})['admitted'][0]
        duplicate = child(); duplicate['support_condition'] += ' Modified after evidence.'
        result = agents.enqueue_children(self.db, parent['id'], [duplicate], {'s1'})
        self.assertEqual(result['existing'], [first])
        self.assertEqual(agents.task(self.db, first)['support_condition'], child()['support_condition'])

    def test_child_reusing_parent_key_with_new_question_is_admitted_and_replay_safe(self):
        self.setup_plan(); parent = self.begin()
        proposal = child(parent['key'], parent['role'])
        proposal['question'] = 'Какие независимые истории показывают оплаченный обход нерешённых возвратов после встроенной синхронизации?'
        before = agents.task(self.db, parent['id'])
        result = agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})
        self.assertEqual(result['existing'], [])
        self.assertEqual(len(result['admitted']), 1)
        identity = result['admitted'][0]
        saved = agents.task(self.db, identity)
        self.assertEqual(saved['parent_task_id'], parent['id'])
        self.assertEqual(saved['key'], parent['key'])
        self.assertNotEqual(saved['stable_key'], parent['stable_key'])
        self.assertNotEqual(saved['question_key'], parent['question_key'])
        self.assertEqual(agents.task(self.db, parent['id']), before)
        self.db.commit(); self.db.close(); self.db = sqlite3.connect(self.path)
        replay = agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})
        self.assertEqual(replay['existing'], [identity])
        self.assertEqual(len(agents.snapshot(self.db, 'cycle')['tasks']), 5)

    def test_reused_child_label_keeps_distinct_questions_and_parent_budget(self):
        self.setup_plan(); parent = self.begin()
        proposals = []
        for topic in ('возвраты', 'заказы', 'остатки'):
            proposal = child('same-label')
            proposal['question'] = f'Какие независимые истории подтверждают что {topic} требуют платного ручного обхода?'
            proposals.append(proposal)
        first = agents.enqueue_children(self.db, parent['id'], [proposals[0]], {'s1'})['admitted'][0]
        second = agents.enqueue_children(self.db, parent['id'], [proposals[1]], {'s1'})['admitted'][0]
        self.assertNotEqual(first, second)
        over_budget = agents.enqueue_children(self.db, parent['id'], [proposals[2]], {'s1'})
        self.assertEqual(over_budget['existing'], [])
        self.assertEqual(len(over_budget['deferred']), 1)
        self.assertEqual(agents.task(self.db, over_budget['deferred'][0])['reason'], 'parent_child_limit')

    def test_same_question_with_changed_key_or_role_still_deduplicates_globally(self):
        self.setup_plan(); parent = self.begin()
        proposal = child()
        identity = agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})['admitted'][0]
        self.finish(parent)
        other_parent = self.begin()
        changed = {**proposal, 'key': 'another-label', 'role': 'foreign',
                   'question': '  ' + proposal['question'].upper() + '!!!  '}
        replay = agents.enqueue_children(self.db, other_parent['id'], [changed], {'s1'})
        self.assertEqual(replay['existing'], [identity])
        self.assertEqual(replay['admitted'], [])
        self.assertEqual(agents.task(self.db, identity)['parent_task_id'], parent['id'])
        # Repeating a root's actual question is also still a duplicate.
        changed['question'] = parent['question']
        self.assertEqual(agents.enqueue_children(self.db, other_parent['id'], [changed], {'s1'})['existing'], [parent['id']])

    def test_legacy_child_identity_is_immutable_but_label_does_not_hide_new_question(self):
        self.setup_plan(); parent = self.begin()
        proposal = child('legacy-key')
        identity = agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})['admitted'][0]
        legacy_key = hashlib.sha256((proposal['role'] + ':' + agents._norm(proposal['key'])).encode()).hexdigest()
        self.db.execute('UPDATE demand_agent_tasks SET stable_key=? WHERE id=?', (legacy_key, identity))
        before = agents.task(self.db, identity)
        replay = agents.enqueue_children(self.db, parent['id'], [{**proposal, 'key': 'renamed'}], {'s1'})
        self.assertEqual(replay['existing'], [identity])
        self.assertEqual(agents.task(self.db, identity), before)
        distinct = {**proposal, 'question': 'Какие конкретные причины заставляют продавцов оплачивать ручную сверку возвратов отдельно?'}
        created = agents.enqueue_children(self.db, parent['id'], [distinct], {'s1'})['admitted'][0]
        self.assertNotEqual(created, identity)
        self.assertEqual(agents.task(self.db, identity), before)

    def test_child_quote_word_limit_and_rejection_keep_bounded_public_question(self):
        self.setup_plan(); parent = self.begin()
        quote = ' '.join(f'word{index}' for index in range(26))
        self.db.execute('UPDATE demand_sources SET text=? WHERE id=?', (quote, 's1'))
        proposal = child(); proposal['trigger_quote'] = quote
        result = agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})
        self.assertEqual(result['rejected'][0]['reason'], 'agent_child_quote_too_long')
        event = json.loads(self.db.execute("SELECT detail FROM demand_agent_events WHERE event='child_rejected'").fetchone()[0])
        self.assertEqual(event['proposal'], {key: proposal[key] for key in ('key', 'role', 'question')})
        self.assertNotIn(quote, json.dumps(event))
        proposal['trigger_quote'] = ' '.join(quote.split()[:25])
        self.assertEqual(len(agents.enqueue_children(self.db, parent['id'], [proposal], {'s1'})['admitted']), 1)

    def test_coordinator_defer_stays_actionable_without_starting_child(self):
        self.setup_plan(); parent = self.begin()
        result = agents.enqueue_children(self.db, parent['id'], [child()], {'s1'}, defer_reason='orchestrator_deferred')
        self.assertEqual(result['admitted'], [])
        row = agents.task(self.db, result['deferred'][0])
        self.assertEqual((row['status'], row['reason'], row['review_needed']), ('deferred', 'orchestrator_deferred', True))
        self.finish(parent); self.finish_all()
        self.assertIsNone(agents.next_task(self.db, 'cycle'))
        self.assertIn(row['id'], agents.snapshot(self.db, 'cycle')['review_needed'])

    def test_budget_excess_stays_visible_and_final_is_limited(self):
        self.setup_plan(); parent = self.begin()
        result = agents.enqueue_children(self.db, parent['id'], [child('a-first'), child('b-second'), child('c-excess')], {'s1'})
        self.assertEqual(len(result['admitted']), 2)
        self.assertEqual(len(result['deferred']), 1)
        deferred = agents.task(self.db, result['deferred'][0])
        self.assertEqual((deferred['reason'], deferred['review_needed']), ('parent_child_limit', True))
        self.finish(parent); self.finish_all()
        agents.save_synthesis(self.db, 'cycle', {'summary': 'Inconclusive: an important check remains deferred.'})
        view = agents.snapshot(self.db, 'cycle')
        self.assertEqual(view['plan']['status'], 'limited')
        self.assertIn(deferred['id'], view['review_needed'])

    def test_depth_limit_preserves_actionable_question_and_global_cap_is_bounded(self):
        self.setup_plan(); first = self.begin()
        ids = agents.enqueue_children(self.db, first['id'], [child('first-child'), child('second-child')], {'s1'})['admitted']
        self.finish(first)
        # Finish other initial specialists; the first child then owns its descendants.
        while agents.next_task(self.db, 'cycle')['id'] != ids[0]:
            self.finish(self.begin())
        parent = self.begin()
        grandchildren = agents.enqueue_children(self.db, parent['id'], [child('grand-one'), child('grand-two')], {'s1'})['admitted']
        self.finish(parent)
        while agents.next_task(self.db, 'cycle')['id'] != grandchildren[0]:
            self.finish(self.begin())
        deepest = self.begin()
        limited = agents.enqueue_children(self.db, deepest['id'], [child('too-deep')], {'s1'})['deferred'][0]
        self.assertEqual(agents.task(self.db, limited)['reason'], 'depth_limit')
        view = agents.snapshot(self.db, 'cycle')
        self.assertEqual(sum(item['status'] != 'deferred' for item in view['tasks']), 8)
        self.assertEqual(agents.task(self.db, limited)['depth'], 3)

    def test_result_requires_read_sources_and_is_idempotent_not_rewritable(self):
        self.setup_plan(); row = self.begin()
        with self.assertRaisesRegex(ValueError, 'sources_not_read'):
            agents.finish_task(self.db, row['id'], {}, ['foreign'], {'foreign'})
        finished = self.finish(row)
        self.assertEqual(self.finish(row), finished)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            agents.finish_task(self.db, row['id'], {'summary': 'Changed verdict'}, ['s1'], {'s1'})

    def test_global_child_budget_cannot_be_bypassed_with_different_parents(self):
        self.setup_plan()
        for index in range(2):
            parent = self.begin()
            result = agents.enqueue_children(self.db, parent['id'],
                [child(f'group-{index}-one'), child(f'group-{index}-two')], {'s1'})
            self.assertEqual(len(result['admitted']), 2)
            self.finish(parent)
        third = self.begin()
        result = agents.enqueue_children(self.db, third['id'], [child('over-cycle-budget')], {'s1'})
        self.assertEqual(result['admitted'], [])
        self.assertEqual(agents.task(self.db, result['deferred'][0])['reason'], 'cycle_child_limit')

    def test_review_can_atomically_record_admission_but_final_cannot_skip_it(self):
        self.setup_plan(); self.finish_all()
        critic = next(row for row in agents.snapshot(self.db, 'cycle')['tasks'] if row['role'] == 'critic')
        admission = agents.enqueue_children(self.db, critic['id'], [child('review-admission')], {'s1'}, synthesis_round=1)
        result = {'summary': 'Conflicting evidence needs the admitted check.', 'admission': admission}
        agents.save_synthesis(self.db, 'cycle', result, status='review', review_round=1)
        self.assertEqual(agents.snapshot(self.db, 'cycle')['syntheses'][0]['result']['admission'], admission)
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            agents.save_synthesis(self.db, 'cycle', {}, review_round=2)

    def test_failed_critic_leaves_explicit_limit_instead_of_claiming_complete(self):
        self.setup_plan()
        while agents.next_task(self.db, 'cycle'):
            row = self.begin()
            self.finish(row, 'failed' if row['role'] == 'critic' else 'done')
        agents.save_synthesis(self.db, 'cycle', {'summary': 'Critic failed; no positive demand conclusion.'})
        view = agents.snapshot(self.db, 'cycle')
        self.assertEqual(view['plan']['status'], 'limited')
        self.assertEqual(len(view['review_needed']), 1)

    def test_failed_synthesis_is_terminal_limited_and_idempotent(self):
        self.setup_plan()
        payload = {'failed': True, 'summary': 'Model failed after three bounded attempts.'}
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            agents.save_synthesis(self.db, 'cycle', payload, status='failed', review_round=1)
        self.finish_all()
        saved = agents.save_synthesis(self.db, 'cycle', payload, status='failed', review_round=1)
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(agents.save_synthesis(self.db, 'cycle', payload, status='failed', review_round=1), saved)
        view = agents.snapshot(self.db, 'cycle')
        self.assertEqual(view['plan']['status'], 'limited')
        self.assertTrue(view['limited'])
        self.assertTrue(view['synthesis_failed'])
        with self.assertRaisesRegex(ValueError, 'already_final'):
            agents.save_synthesis(self.db, 'cycle', {}, status='final', review_round=2)
        with self.assertRaisesRegex(ValueError, 'finalized'):
            agents.enqueue_children(self.db, view['tasks'][-1]['id'], [child('after-failure')], {'s1'})

    def test_synthesis_requires_completed_roles_and_two_rounds_bound_children(self):
        self.setup_plan()
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            agents.save_synthesis(self.db, 'cycle', {})
        self.finish_all()
        review = {'summary': 'Review contradictory documentation with one focused test.'}
        saved = agents.save_synthesis(self.db, 'cycle', review, status='review', review_round=1)
        self.assertEqual(agents.save_synthesis(self.db, 'cycle', review, status='review', review_round=1), saved)
        critic = next(row for row in agents.snapshot(self.db, 'cycle')['tasks'] if row['role'] == 'critic')
        child_id = agents.enqueue_children(self.db, critic['id'], [child('conflict-check')], {'s1'}, synthesis_round=1)['admitted'][0]
        self.assertEqual(self.begin()['id'], child_id)
        self.finish(agents.task(self.db, child_id))
        deferred = agents.enqueue_children(self.db, critic['id'], [child('third-round')], {'s1'}, synthesis_round=2)['deferred'][0]
        self.assertEqual(agents.task(self.db, deferred)['reason'], 'synthesis_round_limit')
        agents.save_synthesis(self.db, 'cycle', {'summary': 'Unresolved test is explicitly limited.'}, review_round=2)
        with self.assertRaisesRegex(ValueError, 'finalized'):
            agents.enqueue_children(self.db, critic['id'], [child('late-child')], {'s1'})
        with self.assertRaisesRegex(ValueError, 'round_limit'):
            agents.save_synthesis(self.db, 'cycle', {}, review_round=3)


if __name__ == '__main__':
    unittest.main()
