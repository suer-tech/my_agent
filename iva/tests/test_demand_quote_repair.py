"""Literal citation repairs on isolated persisted research checkpoints."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import demand_agents as agents
from iva_integration import demand_quote_repair as repair
from iva_integration import demand_research as research
from tests.test_demand_agents import child, research_plan


ORIGINAL = 'Once their order has been fully paid for and delivered, and the legal 14-day withdrawal period has passed'
TRANSLATED = 'после того, как их заказ будет полностью оплачен и доставлен, а установленный законом 14-дневный период отказа истечёт'
SECOND = 'We still reconcile imported orders manually every day.'
STAMP = '2026-10-25T14:00:00+00:00'


class QuoteRepairTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'isolated-research.sqlite3'
        self.store = research.Research(self.path, Path(self.directory.name) / 'cards')
        self.addCleanup(lambda: self.store.close())
        with self.store.db:
            self.store.db.execute('''INSERT INTO demand_cycles
                (id,job_id,slot,sequence,track,topic,task,stage,state,created,updated,plan)
                VALUES('cycle','job','hour',0,'software','Exact original citation fixture','Research fixture',
                       'agent_review','running',?,?,?)''',
                (STAMP, STAMP, json.dumps({'version': 3, 'breadth_goal': 1})))
            plan = research_plan()
            plan['researchers'].sort(key=lambda item: item['role'] != 'foreign')
            agents.save_plan(self.store.db, 'cycle', 'job', 'software', plan)
            self.parent = agents.start_task(self.store.db, agents.next_task(self.store.db, 'cycle')['id'])
            self.store.db.execute('''INSERT INTO demand_batches(cycle_id,batch_index,focus,state,agent_task_id)
                VALUES('cycle',0,'Isolated source reading','done',?)''', (self.parent['id'],))
            for identity, body in [('s1', ORIGINAL + ', your reward is confirmed.'), ('s2', SECOND)]:
                url = 'https://example.test/' + identity
                self.store.db.execute('INSERT INTO demand_sources VALUES(?,?,?,?,?,?,NULL)',
                                      (identity, url, 'Fixture source', body, research.digest(body), STAMP))
                self.store.db.execute('''INSERT INTO demand_candidates
                    (cycle_id,url,purpose,source_type,state,source_id,analysis_state,batch_index)
                    VALUES('cycle',?,'support','request','read',?,'done',0)''', (url, identity))
        self.cycle = self.store.rows("SELECT * FROM demand_cycles WHERE id='cycle'")[0]
        self.sources = research._agent_sources(self.store, 'cycle', self.parent['id'])
        self.bad = {**child('foreign-referral-outcomes', 'foreign'), 'trigger_quote': TRANSLATED}
        self.good = {**child('valid-other-question', 'foreign'), 'source_ids': ['s2'], 'trigger_quote': SECOND,
                     'question': 'Какие дополнительные ограничения импорта сохраняются после ручной сверки заказов?'}

    def reopen(self):
        self.store.close()
        self.store = research.Research(self.path, Path(self.directory.name) / 'cards')

    def report(self, requests):
        value = {'summary': 'Completed foreign-source analysis; referral payouts still need independent checking.',
                 'findings': [], 'conflicts': [], 'limitations': [], 'child_requests': requests,
                 'branch_reason': 'An independent follow-up can change the assessment of the described referral mechanism.'}
        with self.store.db:
            self.store.event('cycle', 'agents', 'role_report',
                             {'task_id': self.parent['id'], 'report': value, 'source_ids': list(self.sources)})
        return copy.deepcopy(value)

    def response(self, quote=ORIGINAL, **updates):
        return {'key': self.bad['key'], 'status': 'repaired', 'source_ids': ['s1'],
                'trigger_quote': quote, 'reason': 'The original sentence expresses the same condition before confirming a referral payout.',
                **updates}

    def cli(self, responses):
        class Client:
            calls = []
            def structured(inner, prompt, schema):
                inner.calls.append((prompt, schema))
                response = responses[len(inner.calls) - 1]
                if isinstance(response, BaseException):
                    raise response
                return copy.deepcopy(response)
        return Client()

    def review_cli(self, expected_keys):
        return self.cli([{'summary': 'The corrected request remains a useful independent follow-up.',
                          'decisions': [{'key': key, 'decision': 'approve',
                                         'reason': 'The original citation justifies checking this specific uncertainty.'}
                                        for key in expected_keys]}])

    def events(self):
        return [json.loads(row['detail']) for row in self.store.rows(
            "SELECT detail FROM demand_events WHERE event='child_quote_repair' ORDER BY id")]

    def test_real_translated_quote_is_repaired_and_persisted_before_review(self):
        original = self.report([self.good, self.bad])
        cli = self.cli([self.response()])
        result = research._agent_review(self.store, self.cycle, cli)
        self.assertEqual(result['stage'], 'agent_review')
        self.assertEqual(len(cli.calls), 1)
        self.assertEqual(agents.task(self.store.db, self.parent['id'])['status'], 'running')
        self.assertEqual(self.store.rows('SELECT id FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL'), [])
        self.assertIn('НА ЯЗЫКЕ ОРИГИНАЛА', cli.calls[0][0])
        self.assertEqual([event['outcome'] for event in self.events()], ['started', 'repaired'])
        self.reopen()
        review = self.review_cli([self.good['key'], self.bad['key']])
        research._agent_review(self.store, self.cycle, review)
        saved = agents.task(self.store.db, self.parent['id'])
        self.assertEqual(saved['status'], 'done')
        self.assertEqual(saved['result']['child_limitations'], [])
        children = self.store.rows('SELECT payload FROM demand_agent_tasks WHERE parent_task_id=?', (self.parent['id'],))
        self.assertEqual(len(children), 2)
        repaired = next(json.loads(row['payload']) for row in children if json.loads(row['payload'])['key'] == self.bad['key'])
        self.assertEqual(repaired['trigger_quote'], ORIGINAL)
        self.assertEqual({key: value for key, value in repaired.items() if key not in ('source_ids', 'trigger_quote')},
                         {key: value for key, value in self.bad.items() if key not in ('source_ids', 'trigger_quote')})
        stored_report = research._agent_events(self.store, 'cycle', 'role_report', self.parent['id'])[0]['report']
        self.assertEqual(stored_report, original)  # Preserve the initial answer for audit.

    def test_valid_decorative_original_requires_no_model_repair(self):
        proposal = {**self.bad, 'trigger_quote': '«' + ORIGINAL + '»'}
        cli = self.cli([])
        result = repair.repair_step(self.store, self.cycle, self.parent, [proposal], self.sources, cli)
        self.assertFalse(result['pending'])
        self.assertEqual(result['requests'][0]['trigger_quote'], proposal['trigger_quote'])
        self.assertEqual(cli.calls, [])
        with self.store.db:
            admitted = agents.enqueue_children(self.store.db, self.parent['id'], result['requests'], self.sources)
        self.assertEqual(agents.task(self.store.db, admitted['admitted'][0])['trigger_quote'], ORIGINAL)
        self.assertEqual(len(self.store.rows("SELECT id FROM demand_agent_events WHERE event='child_quote_normalized'")), 1)

    def test_two_invented_corrections_become_limitation_without_parent_execution_failure(self):
        self.report([self.bad])
        cli = self.cli([self.response('This invented quotation never appeared in the actual referral source.')] * 2)
        for _ in range(2):
            self.assertEqual(research._agent_review(self.store, self.cycle, cli)['stage'], 'agent_review')
            self.reopen()
        review = self.review_cli([])
        research._agent_review(self.store, self.cycle, review)
        saved = agents.task(self.store.db, self.parent['id'])
        self.assertEqual(saved['status'], 'done')
        self.assertEqual(saved['result']['child_limitations'][0]['reason'], 'quote_repair_exhausted')
        self.assertEqual(self.store.rows('SELECT id FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL'), [])
        snapshot = agents.snapshot(self.store.db, 'cycle')
        self.assertTrue(snapshot['limited'])
        self.assertIn(self.parent['id'], snapshot['review_needed'])
        self.assertEqual(snapshot['counts']['failed'], 0)
        coverage = research._final_coverage(self.store, self.cycle)
        self.assertEqual(coverage['child_request_limits'][0]['reason'], 'quote_repair_exhausted')
        self.assertTrue(any('основные отчёты сохранены' in item for item in coverage['analysis_gaps']))
        self.assertEqual(len(cli.calls), 2)

    def test_unsupported_answer_is_not_retried_or_admitted(self):
        cli = self.cli([self.response(status='unsupported', source_ids=[], trigger_quote='')])
        first = repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, cli)
        self.assertTrue(first['pending'])
        self.reopen()
        result = repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([]))
        self.assertFalse(result['pending'])
        self.assertEqual(result['requests'], [])
        self.assertEqual(result['limitations'][0]['reason'], 'quote_repair_unsupported')

    def test_response_cannot_change_question_or_choose_unread_source(self):
        for change in ({'question': 'A different market question'}, {'source_ids': ['unread']}):
            with self.subTest(change=change):
                request = {**self.bad, 'key': 'request-' + next(iter(change))}
                response = self.response(key=request['key'], **change)
                result = repair.repair_step(self.store, self.cycle, self.parent, [request], self.sources, self.cli([response]))
                self.assertTrue(result['pending'])
                self.assertEqual(self.events()[-1]['outcome'], 'invalid')

    def test_source_id_typo_can_be_corrected_only_to_a_read_source(self):
        request = {**self.bad, 'source_ids': ['observation-not-source']}
        repair.repair_step(self.store, self.cycle, self.parent, [request], self.sources, self.cli([self.response()]))
        result = repair.repair_step(self.store, self.cycle, self.parent, [request], self.sources, self.cli([]))
        self.assertEqual(result['requests'][0]['source_ids'], ['s1'])

    def test_native_repair_schema_omits_unsupported_unique_items_but_valid_correction_works(self):
        cli = self.cli([self.response()])
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, cli)
        # The transmitted native schema must stay within its supported subset.
        self.assertNotIn('uniqueItems', json.dumps(cli.calls[0][1]))
        self.reopen()
        result = repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([]))
        self.assertEqual(result['requests'][0]['trigger_quote'], ORIGINAL)

    def test_duplicate_source_ids_are_rejected_locally_even_without_native_schema_keyword(self):
        cli = self.cli([self.response(source_ids=['s1', 's1']), self.response()])
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, cli)
        self.assertEqual(self.events()[-1]['outcome'], 'invalid')
        self.assertEqual(self.events()[-1]['category'], 'repair_source_ids_must_be_unique')
        self.reopen()
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, cli)
        self.reopen()
        result = repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([]))
        self.assertEqual(result['requests'][0]['source_ids'], ['s1'])
        self.assertEqual([item['outcome'] for item in self.events()], ['started', 'invalid', 'started', 'repaired'])

    def test_overlong_literal_attempt_supplies_persisted_word_count_to_next_correction(self):
        long_quote = ORIGINAL + ' and then the resulting reward will become payable to the qualifying customer'
        self.sources['s1']['text'] = long_quote
        cli = self.cli([self.response(long_quote), self.response()])
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, cli)
        rejected = self.events()[-1]
        self.assertEqual(rejected['category'], 'agent_child_quote_too_long')
        self.assertEqual(rejected['word_count'], len(long_quote.split()))
        self.assertGreater(rejected['word_count'], rejected['word_limit'])
        self.assertEqual(rejected['attempted_citation']['trigger_quote'], long_quote)
        self.reopen()
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, cli)
        feedback = json.loads(cli.calls[1][0].split('\nПредыдущая проверка: ', 1)[1].split('\nРазрешённые прочитанные источники: ', 1)[0])
        self.assertEqual(feedback, rejected)
        result = repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([]))
        self.assertEqual(result['requests'][0]['trigger_quote'], ORIGINAL)

    def test_unknown_validator_exception_text_is_not_persisted(self):
        validate = agents.validate_child_proposal
        def unknown_failure(value, sources):
            if value['trigger_quote'] == ORIGINAL:
                raise ValueError('PRIVATE_UNRECOGNIZED_EXCEPTION_CONTENT')
            return validate(value, sources)
        with patch.object(agents, 'validate_child_proposal', side_effect=unknown_failure):
            repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([self.response()]))
        self.assertEqual(self.events()[-1]['category'], 'repair_citation_not_verified')
        self.assertNotIn('PRIVATE_UNRECOGNIZED', json.dumps(self.events()))

    def test_schema_invalid_response_does_not_persist_unbounded_attempt(self):
        invalid = self.response('UNBOUNDED_RESPONSE_MARKER ' * 100)
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([invalid]))
        self.assertEqual(self.events()[-1]['category'], 'repair_response_schema_invalid')
        self.assertNotIn('attempted_citation', self.events()[-1])
        self.assertNotIn('UNBOUNDED_RESPONSE_MARKER', json.dumps(self.events()))

    def test_persisted_correction_still_checks_source_id_uniqueness(self):
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([self.response()]))
        row = self.store.rows("SELECT id,detail FROM demand_events WHERE event='child_quote_repair' ORDER BY id DESC LIMIT 1")[0]
        detail = json.loads(row['detail'])
        detail['citation']['source_ids'] = ['s1', 's1']
        with self.store.db:
            self.store.db.execute('UPDATE demand_events SET detail=? WHERE id=?', (json.dumps(detail), row['id']))
        self.reopen()
        result = repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([]))
        self.assertEqual(result['requests'], [])
        self.assertEqual(result['limitations'][0]['reason'], 'quote_repair_saved_citation_no_longer_valid')

    def test_started_attempt_survives_crash_and_retry_does_not_duplicate_children(self):
        self.report([self.bad])
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            research._agent_review(self.store, self.cycle, self.cli([Crash()]))
        self.reopen()
        self.assertEqual(len(self.events()), 1)
        research._agent_review(self.store, self.cycle, self.cli([self.response()]))
        self.reopen()
        research._agent_review(self.store, self.cycle, self.review_cli([self.bad['key']]))
        self.assertEqual(len(self.store.rows('SELECT id FROM demand_agent_tasks WHERE parent_task_id=?', (self.parent['id'],))), 1)
        self.assertEqual([row['attempt'] for row in self.events() if row['outcome'] == 'started'], [1, 2])

    def test_committed_repair_is_reused_after_crash_without_second_model_call(self):
        self.report([self.bad])
        record = repair._record
        class Crash(BaseException):
            pass
        def crash_after_commit(*args, **kwargs):
            record(*args, **kwargs)
            if kwargs.get('outcome') == 'repaired':
                raise Crash()
        with patch.object(repair, '_record', side_effect=crash_after_commit), self.assertRaises(Crash):
            research._agent_review(self.store, self.cycle, self.cli([self.response()]))
        self.reopen()
        review = self.review_cli([self.bad['key']])
        research._agent_review(self.store, self.cycle, review)
        self.assertEqual(len(review.calls), 1)
        self.assertIn('decisions', review.calls[0][1]['properties'])
        self.assertEqual(len([row for row in self.events() if row['outcome'] == 'started']), 1)

    def test_repeated_model_transport_failure_is_not_hidden_as_semantic_success(self):
        self.report([self.bad])
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'model timeout'):
                research._agent_review(self.store, self.cycle, self.cli([RuntimeError('model timeout')]))
            self.reopen()
        with self.assertRaises(repair.QuoteRepairUnavailable):
            research._agent_review(self.store, self.cycle, self.cli([]))
        self.assertEqual(agents.task(self.store.db, self.parent['id'])['status'], 'running')
        self.assertEqual(self.store.rows('SELECT id FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL'), [])

    def test_saved_citation_is_rechecked_before_using_it(self):
        repair.repair_step(self.store, self.cycle, self.parent, [self.bad], self.sources, self.cli([self.response()]))
        changed = {**self.sources, 's1': {**self.sources['s1'], 'text': 'A changed source body contains no former citation.'}}
        result = repair.repair_step(self.store, self.cycle, self.parent, [self.bad], changed, self.cli([]))
        self.assertEqual(result['requests'], [])
        self.assertEqual(result['limitations'][0]['reason'], 'quote_repair_saved_citation_no_longer_valid')

    def synthesis_fixture(self, requests):
        with self.store.db:
            self.store.db.execute("UPDATE demand_agent_tasks SET status='done',result='{}' WHERE cycle_id='cycle'")
            self.store.db.execute("UPDATE demand_cycles SET stage='agent_synthesis' WHERE id='cycle'")
        self.cycle = self.store.rows("SELECT * FROM demand_cycles WHERE id='cycle'")[0]
        return {'summary': 'Independent research reports preserve an unresolved referral uncertainty.',
                'decisions': [], 'conflicts': [], 'limitations': [], 'child_requests': requests,
                'branch_reason': 'The original source describes a payout condition but independent actual outcomes are unknown.'}

    def test_synthesis_repairs_saved_proposal_without_regenerating_it_after_restart(self):
        original = self.synthesis_fixture([self.bad, self.good])
        cli = self.cli([original, self.response()])
        first = research._agent_synthesis(self.store, self.cycle, cli)
        self.assertEqual(first['stage'], 'agent_synthesis')
        self.assertEqual(len(cli.calls), 2)
        self.assertEqual(len(research._agent_events(self.store, 'cycle', 'synthesis_proposed')), 1)
        self.reopen()
        semantic = self.review_cli([self.bad['key']])
        pending = research._agent_synthesis(self.store, self.cycle, semantic)
        self.assertEqual(pending['stage'], 'agent_synthesis')
        self.assertEqual(len(semantic.calls), 1)
        self.assertIn('исправленных цитат', semantic.calls[0][0])
        self.assertEqual(len([item for item in agents.snapshot(self.store.db, 'cycle')['tasks'] if item['parent_task_id']]), 0)
        self.reopen()
        research._agent_synthesis(self.store, self.cycle, self.cli([]))
        snapshot = agents.snapshot(self.store.db, 'cycle')
        self.assertEqual(len(snapshot['syntheses']), 1)
        self.assertEqual(snapshot['syntheses'][0]['status'], 'review')
        self.assertEqual(len([item for item in snapshot['tasks'] if item['parent_task_id']]), 2)
        repaired = next(item for item in snapshot['syntheses'][0]['result']['child_requests'] if item['key'] == self.bad['key'])
        self.assertEqual(repaired['trigger_quote'], ORIGINAL)
        self.assertEqual(research._agent_events(self.store, 'cycle', 'synthesis_proposed')[0]['result']['child_requests'],
                         original['child_requests'])
        self.assertEqual(len(research._agent_events(self.store, 'cycle', 'synthesis_quote_review')), 2)

    def test_synthesis_literal_but_unrelated_replacement_requires_new_semantic_approval(self):
        self.bad.update(question='Какой фактический срок возврата товара требуется для выплаты реферальной комиссии?',
                        why_needed='Исходный текст описывает срок возврата и выплату реферальной комиссии, их нужно сопоставить с реальным опытом.')
        original = self.synthesis_fixture([self.bad])
        wrong = self.response(quote=SECOND, source_ids=['s2'])
        research._agent_synthesis(self.store, self.cycle, self.cli([original, wrong]))
        rejection = {'summary': 'The replacement is literal but concerns a different business process.',
                     'decisions': [{'key': self.bad['key'], 'decision': 'reject',
                                    'reason': 'Manual imported-order reconciliation does not establish any referral payout condition.'}]}
        semantic = self.cli([rejection])
        research._agent_synthesis(self.store, self.cycle, semantic)
        self.assertIn(SECOND, semantic.calls[0][0])
        self.assertIn(self.bad['question'], semantic.calls[0][0])
        self.reopen()
        research._agent_synthesis(self.store, self.cycle, self.cli([]))
        snapshot = agents.snapshot(self.store.db, 'cycle')
        self.assertEqual([item for item in snapshot['tasks'] if item['parent_task_id']], [])
        self.assertEqual(snapshot['syntheses'][0]['result']['child_limitations'][0]['reason'], 'quote_repair_semantic_reject')
        self.assertEqual(snapshot['counts']['failed'], 0)
        self.assertEqual(snapshot['plan']['status'], 'limited')

    def test_synthesis_semantic_review_invalid_outputs_exhaust_without_new_synthesis_or_child(self):
        original = self.synthesis_fixture([self.bad])
        research._agent_synthesis(self.store, self.cycle, self.cli([original, self.response()]))
        for _ in range(2):
            research._agent_synthesis(self.store, self.cycle, self.cli([{'summary': 'Wrong keys must not imply approval.', 'decisions': []}]))
            self.reopen()
        research._agent_synthesis(self.store, self.cycle, self.cli([]))
        snapshot = agents.snapshot(self.store.db, 'cycle')
        self.assertEqual(snapshot['syntheses'][0]['result']['child_limitations'][0]['reason'], 'quote_repair_semantic_review_exhausted')
        self.assertEqual([item for item in snapshot['tasks'] if item['parent_task_id']], [])
        self.assertEqual(len(research._agent_events(self.store, 'cycle', 'synthesis_proposed')), 1)
        events = research._agent_events(self.store, 'cycle', 'synthesis_quote_review')
        self.assertEqual([item['outcome'] for item in events], ['started', 'invalid', 'started', 'invalid'])

    def test_synthesis_semantic_review_transport_fault_remains_technical_and_bounded(self):
        original = self.synthesis_fixture([self.bad])
        research._agent_synthesis(self.store, self.cycle, self.cli([original, self.response()]))
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'model timeout'):
                research._agent_synthesis(self.store, self.cycle, self.cli([RuntimeError('model timeout')]))
            self.reopen()
        with self.assertRaises(repair.QuoteRepairUnavailable):
            research._agent_synthesis(self.store, self.cycle, self.cli([]))
        self.assertEqual(agents.snapshot(self.store.db, 'cycle')['syntheses'], [])

    def test_synthesis_semantic_review_crash_consumes_attempt_and_reuses_committed_approval(self):
        original = self.synthesis_fixture([self.bad])
        research._agent_synthesis(self.store, self.cycle, self.cli([original, self.response()]))
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            research._agent_synthesis(self.store, self.cycle, self.cli([Crash()]))
        self.reopen()
        research._agent_synthesis(self.store, self.cycle, self.review_cli([self.bad['key']]))
        self.reopen()
        research._agent_synthesis(self.store, self.cycle, self.cli([]))
        snapshot = agents.snapshot(self.store.db, 'cycle')
        self.assertEqual(len([item for item in snapshot['tasks'] if item['parent_task_id']]), 1)
        events = research._agent_events(self.store, 'cycle', 'synthesis_quote_review')
        self.assertEqual([item['attempt'] for item in events if item['outcome'] == 'started'], [1, 2])
        self.assertEqual(len(research._agent_events(self.store, 'cycle', 'synthesis_proposed')), 1)

    def test_synthesis_exhaustion_records_gap_without_claiming_execution_failed(self):
        original = self.synthesis_fixture([self.bad])
        invalid = self.response('This made-up original quotation never appeared on the source page.')
        research._agent_synthesis(self.store, self.cycle, self.cli([original, invalid]))
        self.reopen()
        research._agent_synthesis(self.store, self.cycle, self.cli([invalid]))
        self.reopen()
        research._agent_synthesis(self.store, self.cycle, self.cli([]))
        snapshot = agents.snapshot(self.store.db, 'cycle')
        self.assertEqual(snapshot['counts']['failed'], 0)
        self.assertEqual(snapshot['syntheses'][0]['status'], 'final')
        self.assertEqual(snapshot['plan']['status'], 'limited')
        self.assertIn('synthesis_child_grounding', snapshot['review_needed'])
        self.assertEqual(snapshot['syntheses'][0]['result']['child_limitations'][0]['reason'], 'quote_repair_exhausted')
        self.assertEqual(len(research._agent_events(self.store, 'cycle', 'synthesis_proposed')), 1)
        self.assertEqual(len([item for item in self.events() if item['outcome'] == 'started']), 2)
        self.assertEqual(research._final_coverage(self.store, self.cycle)['child_request_limits'][0]['reason'],
                         'quote_repair_exhausted')

    def test_already_admitted_valid_sibling_is_retained_and_not_duplicated_after_repair(self):
        self.report([self.good, self.bad])
        with self.store.db:
            old = agents.enqueue_children(self.store.db, self.parent['id'], [self.good], self.sources)
        research._agent_review(self.store, self.cycle, self.cli([self.response()]))
        self.reopen()
        research._agent_review(self.store, self.cycle, self.review_cli([self.good['key'], self.bad['key']]))
        saved = agents.task(self.store.db, self.parent['id'])
        self.assertEqual(saved['result']['children']['existing'], old['admitted'])
        self.assertEqual(len(saved['result']['children']['admitted']), 1)
        self.assertEqual(len(self.store.rows('SELECT id FROM demand_agent_tasks WHERE parent_task_id=?', (self.parent['id'],))), 2)

    def test_unverified_child_keeps_desk_gate_closed_even_with_other_evidence(self):
        self.report([self.bad])
        research._agent_review(self.store, self.cycle, self.cli([self.response(status='unsupported', source_ids=[], trigger_quote='')]))
        research._agent_review(self.store, self.cycle, self.review_cli([]))
        snapshot = agents.snapshot(self.store.db, 'cycle')
        coverage = {'agent_progress': {'review_needed': snapshot['review_needed']},
                    'unique_read_pages': 30, 'unique_analyzed_pages': 30, 'counter_search_completed': True}
        evidence = [{'kind': 'problem', 'source_type': 'complaint'},
                    {'kind': 'demand', 'source_type': 'request'}, {'kind': 'alternative', 'source_type': 'offer'}]
        with patch.object(self.store, 'evidence', return_value=evidence), patch.object(research, '_independent', return_value=3), \
                patch.object(research, '_agent_events', return_value=[{'result': {'decisions': [{'hypothesis_id': 'h', 'disposition': 'investigate'}]}}]):
            quality = research._quality(self.store, self.cycle, 'h', coverage)
        self.assertFalse(quality['desk_gate_passed'])
        self.assertFalse(quality['orchestrator_approved'])


if __name__ == '__main__':
    unittest.main()
