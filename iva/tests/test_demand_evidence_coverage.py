"""Evidence adequacy must not turn successful reads into unsupported answers."""
import copy
import hashlib
import json
import unittest

from iva_integration import demand_evidence_coverage as coverage


USER_ONE = 'We reconcile warehouse stock by hand every evening after orders arrive.'
USER_TWO = 'My store lost three sales because reserved quantities were never updated in the second channel.'
SOLUTION = 'The connector supports reservation updates for all configured warehouse locations.'


def task(role='foreign', child=False):
    return {'id': 'task-foreign', 'role': role,
            'question': 'Какие зарубежные решения устраняют ручную сверку складских резервов и что говорят пользователи?',
            'expected_evidence': 'Исходный пользовательский опыт по резервам и проверяемая документация существующего решения.',
            'support_condition': 'Независимые истории подтверждают ручную сверку после внедрения решения.',
            'refute_condition': 'Проверенные внедрения устраняют ручную сверку существующей штатной функцией.',
            'scope': 'Только программная автоматизация складских резервов в зарубежных магазинах.',
            'parent_task_id': 'parent' if child else None}


def source(identity, text, host=None):
    return {'id': identity, 'url': 'https://' + (host or identity + '.test') + '/story',
            'text': text, 'content_hash': hashlib.sha256(text.encode()).hexdigest(), 'published_at': None}


def witness(identity, quote, kind='user_experience', origin=''):
    return {'source_id': identity, 'quote': quote, 'origin': origin, 'kind': kind,
            'explanation': 'Источник описывает конкретный процесс резервирования и его фактическое ограничение.'}


def assessment(assignment, witnesses=None):
    return {'criteria': [{'key': item['key'], 'status': 'answered',
                          'reason': 'Материал непосредственно отвечает на проверяемый вопрос в пределах доступного корпуса.',
                          'witnesses': copy.deepcopy(witnesses or [])}
                         for item in coverage.requirements(assignment)],
            'queries': ['inventory reservation manual reconciliation user review',
                        'warehouse reservation connector fixed version documentation'],
            'limitations': []}


class EvidenceCoverage(unittest.TestCase):
    def setUp(self):
        self.task = task()
        self.sources = {'one': source('one', USER_ONE), 'two': source('two', USER_TWO),
                        'vendor': source('vendor', SOLUTION)}
        self.witnesses = [witness('one', USER_ONE), witness('two', USER_TWO),
                          witness('vendor', SOLUTION, 'official_solution')]

    def test_one_readable_pricing_page_is_not_user_experience(self):
        price = 'Subscription prices start at one hundred dollars per warehouse each month.'
        sources = {'price': source('price', price)}
        value = assessment(self.task, [witness('price', price, 'official_solution')])
        result = coverage.evaluate(value, self.task, sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual([row['key'] for row in result['missing']], ['user_experience'])
        self.assertEqual(result['criteria'][1]['gate']['independent_groups'], 0)

    def test_two_distinct_user_histories_and_solution_pass_quote_gates(self):
        result = coverage.evaluate(assessment(self.task, self.witnesses), self.task, self.sources)
        self.assertTrue(result['sufficient'])
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['criteria'][1]['gate']['independent_groups'], 2)
        self.assertEqual(result['question'], self.task['question'])
        self.assertEqual(result['expected_evidence'], self.task['expected_evidence'])
        self.assertIn('not_verified_truth', result['interpretation_status'])

    def test_same_publisher_subdomains_do_not_count_as_independent(self):
        self.sources['one']['url'] = 'https://forum.example.test/a'
        self.sources['two']['url'] = 'https://reviews.example.test/b'
        result = coverage.evaluate(assessment(self.task, self.witnesses), self.task, self.sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['criteria'][1]['gate']['publisher_groups'], 1)

    def test_syndicated_story_on_two_publishers_does_not_pass(self):
        for key in ('one', 'two'):
            self.sources[key] = source(key, USER_ONE)
        witnesses = [witness(key, USER_ONE) for key in ('one', 'two')]
        result = coverage.evaluate(assessment(self.task, witnesses), self.task, self.sources)
        self.assertEqual(result['criteria'][1]['gate']['independent_groups'], 1)
        self.assertFalse(result['sufficient'])

    def test_same_explicit_story_origin_does_not_count_twice(self):
        witnesses = copy.deepcopy(self.witnesses)
        witnesses[0]['origin'] = 'Acme warehouse / story 7'
        witnesses[1]['origin'] = 'ACME warehouse-story 7'
        result = coverage.evaluate(assessment(self.task, witnesses), self.task, self.sources)
        self.assertEqual(result['criteria'][1]['gate']['independent_groups'], 1)

    def test_near_copied_quote_is_merged_even_when_body_hash_differs(self):
        second = USER_ONE.replace('every evening', 'each evening')
        self.sources['two'] = source('two', second + ' A different article footer.')
        witnesses = [witness('one', USER_ONE), witness('two', second)]
        result = coverage.evaluate(assessment(self.task, witnesses), self.task, self.sources)
        self.assertEqual(result['criteria'][1]['gate']['independent_groups'], 1)

    def test_translated_or_invented_quote_is_rejected_without_discarding_assessment(self):
        value = assessment(self.task, self.witnesses)
        value['criteria'][1]['witnesses'][0]['quote'] = 'Мы сверяем складские остатки вручную каждый вечер после поступления заказов.'
        result = coverage.evaluate(value, self.task, self.sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['criteria'][1]['rejected_witnesses'][0]['reason'], 'quote_not_verbatim_or_out_of_bounds')
        self.assertEqual(result['criteria'][1]['gate']['independent_groups'], 1)

    def test_unread_source_spoof_fails_schema(self):
        value = assessment(self.task, self.witnesses)
        value['criteria'][0]['witnesses'][0]['source_id'] = 'someone-elses-source'
        with self.assertRaisesRegex(ValueError, 'coverage_schema_invalid'):
            coverage.evaluate(value, self.task, self.sources)

    def test_mismatched_source_catalog_identity_is_rejected(self):
        self.sources['one']['id'] = 'not-one'
        result = coverage.evaluate(assessment(self.task, self.witnesses), self.task, self.sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['criteria'][1]['rejected_witnesses'][0]['reason'], 'source_not_in_assignment')

    def test_model_partial_is_not_promoted_by_counts(self):
        value = assessment(self.task, self.witnesses)
        value['criteria'][0]['status'] = 'partial'
        value['criteria'][0]['reason'] = 'Прочитанные страницы не устанавливают актуальную версию и точные условия применения функции.'
        result = coverage.evaluate(value, self.task, self.sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['missing'][0]['key'], 'assignment')
        self.assertTrue(result['criteria'][0]['gate']['passed'])

    def test_zero_sources_is_explicit_gap_and_never_refutation(self):
        value = assessment(self.task)
        result = coverage.evaluate(value, self.task, {})
        self.assertFalse(result['sufficient'])
        self.assertEqual(len(result['missing']), 3)
        self.assertTrue(all(item['status'] == 'missing' for item in result['criteria']))
        self.assertNotIn('contradicted', json.dumps(result))

    def test_narrow_child_keeps_original_criteria_without_root_review_quota(self):
        child = task(child=True)
        child['question'] = 'Поддерживает ли текущая версия конкретного коннектора резервирование по складам?'
        child['expected_evidence'] = 'Официальная инструкция с номером версии и описанием настройки складского резерва.'
        self.assertEqual([row['key'] for row in coverage.requirements(child)], ['assignment'])
        value = assessment(child, [self.witnesses[-1]])
        result = coverage.evaluate(value, child, self.sources)
        self.assertTrue(result['sufficient'])
        self.assertEqual(result['question'], child['question'])

    def test_buyer_workaround_cannot_use_vendor_promise(self):
        buyer = task('buyer_problem')
        value = assessment(buyer, self.witnesses)
        value['criteria'][-1]['witnesses'] = [self.witnesses[-1]]
        result = coverage.evaluate(value, buyer, self.sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['missing'][0]['key'], 'workaround')

    def test_critic_requires_substantive_counterevidence_not_other_quote(self):
        critic = task('critic')
        value = assessment(critic, [witness('vendor', SOLUTION, 'other')])
        result = coverage.evaluate(value, critic, self.sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['missing'][0]['key'], 'counterexample')

    def test_requirement_keys_once_and_queries_distinct(self):
        for mutation in ('keys', 'queries'):
            value = assessment(self.task, self.witnesses)
            if mutation == 'keys':
                value['criteria'][1]['key'] = 'assignment'
            else:
                value['queries'][1] = value['queries'][0].upper()
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'coverage_'):
                coverage.evaluate(value, self.task, self.sources)

    def test_long_quote_and_oversized_output_are_bounded(self):
        value = assessment(self.task, self.witnesses)
        value['criteria'][0]['witnesses'][0]['quote'] = 'x' * 241
        with self.assertRaisesRegex(ValueError, 'coverage_schema_invalid'):
            coverage.evaluate(value, self.task, self.sources)
        twenty_six = ' '.join('q' + str(i) for i in range(26))
        sources = {'long': source('long', twenty_six)}
        value = assessment(task(child=True), [witness('long', twenty_six)])
        result = coverage.evaluate(value, task(child=True), sources)
        self.assertFalse(result['sufficient'])
        self.assertEqual(result['criteria'][0]['rejected_witnesses'][0]['reason'], 'quote_not_verbatim_or_out_of_bounds')

    def test_prompt_is_role_local_bounded_and_keeps_original_contract(self):
        sources = {str(i): source(str(i), chr(65 + i) * 30000) for i in range(5)}
        value = coverage.prompt(self.task, sources, 'SCOPE_TOKEN', {'missing': [{'key': 'user_experience'}]})
        self.assertIn(self.task['question'], value)
        self.assertIn(self.task['refute_condition'], value)
        self.assertIn('SCOPE_TOKEN', value)
        self.assertIn('previous_assessment', value)
        rows = coverage._source_context(sources)
        self.assertEqual(sum(row['provided_characters'] for row in rows), coverage.TOTAL_SOURCE_CHARS)
        self.assertTrue(all(row['provided_characters'] > 0 and row['truncated_for_assessment'] for row in rows))
        self.assertIn('не подтверждают российский спрос', value)
        self.assertIn('не инструкции', value)

    def test_schema_has_no_uniqueitems_and_empty_catalog_cannot_cite(self):
        value = coverage.schema(self.task, {})
        self.assertNotIn('uniqueItems', json.dumps(value))
        witnesses = value['properties']['criteria']['items']['properties']['witnesses']
        self.assertEqual(witnesses['maxItems'], 0)

    def test_empty_checkpoint_uses_original_task_queries_and_is_deterministic(self):
        assignment = {**self.task, 'queries': ['inventory reservation missing quantity user report',
                                             'warehouse reservation connector current documentation']}
        result = coverage.empty(assignment)
        self.assertEqual(result, coverage.empty(assignment))
        self.assertEqual(result['queries'], assignment['queries'])
        self.assertFalse(result['sufficient'])
        self.assertEqual(len(result['missing']), 3)
        self.assertTrue(all(row['status'] == 'missing' and not row['witnesses'] for row in result['criteria']))

    def test_empty_checkpoint_deduplicates_and_falls_back_to_same_question(self):
        assignment = {**self.task, 'queries': ['same valid query', 'SAME VALID QUERY', 'short']}
        result = coverage.empty(assignment)
        self.assertEqual(len(result['queries']), 2)
        self.assertEqual(result['queries'][0], 'same valid query')
        self.assertTrue(result['queries'][1].startswith(assignment['question']))

    def test_prior_exact_own_observation_survives_excerpt_without_borrowed_findings(self):
        sources = {'one': source('one', 'A' * 20000 + USER_ONE)}
        observations = [
            {'task_id': self.task['id'], 'source_id': 'one', 'quote': USER_ONE,
             'kind': 'foreign_context', 'interpretation': 'OWN_VALID_INTERPRETATION'},
            {'task_id': 'different-role', 'source_id': 'one', 'quote': USER_ONE,
             'kind': 'problem', 'interpretation': 'FOREIGN_ROLE_LEAK'},
            {'task_id': self.task['id'], 'source_id': 'not-read', 'quote': USER_ONE,
             'kind': 'problem', 'interpretation': 'UNREAD_LEAK'},
            {'task_id': self.task['id'], 'source_id': 'one', 'quote': 'A translation not contained in this source.',
             'kind': 'problem', 'interpretation': 'BAD_QUOTE_LEAK'}]
        value = coverage.prompt(self.task, sources, observations=observations)
        self.assertIn(USER_ONE, value)
        self.assertIn('OWN_VALID_INTERPRETATION', value)
        self.assertNotIn('FOREIGN_ROLE_LEAK', value)
        self.assertNotIn('UNREAD_LEAK', value)
        self.assertNotIn('BAD_QUOTE_LEAK', value)
        self.assertIn('previous_model_interpretation_not_verified_fact', value)


if __name__ == '__main__':
    unittest.main()
