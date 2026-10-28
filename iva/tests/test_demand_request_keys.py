"""Repeated model labels must not lose distinct, source-grounded child requests."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from iva_integration import demand_agents as agents, demand_research as research, demand_scope as scope
from tests import test_demand_quote_repair as fixtures
from tests.test_demand_agents import child


class ContractCli:
    def __init__(self, synthesis=None, reject_scope=None):
        self.synthesis = synthesis
        self.reject_scope = reject_scope
        self.calls = []

    def structured(self, prompt, schema):
        self.calls.append((prompt, schema))
        props = schema['properties']
        if 'scope_decisions' in props:
            keys = props['scope_decisions']['items']['properties']['key']['enum']
            return {'scope_id': props['scope_id']['enum'][0], 'scope_decisions': [
                {'key': key, 'relationship': 'cross_lane' if key == self.reject_scope else 'same_problem',
                 'reason': 'The proposal stays within the named software workflow and observed uncertainty.'} for key in keys]}
        if 'child_requests' in props:
            return copy.deepcopy(self.synthesis)
        if 'trigger_quote' in props:
            return {'key': props['key']['enum'][0], 'status': 'repaired', 'source_ids': ['s1'],
                    'trigger_quote': fixtures.ORIGINAL,
                    'reason': 'The original sentence supports the same existing source-grounded question.'}
        keys = props['decisions']['items']['properties']['key'].get('enum', [])
        return {'summary': 'Each narrowly scoped follow-up is justified by its own source and question.',
                'decisions': [{'key': key, 'decision': 'approve',
                               'reason': 'This specific uncertainty can change the conclusion about the workflow.'} for key in keys]}


class RequestKeyBindingTests(unittest.TestCase):
    report = fixtures.QuoteRepairTests.report
    reopen = fixtures.QuoteRepairTests.reopen
    synthesis_fixture = fixtures.QuoteRepairTests.synthesis_fixture

    def setUp(self):
        fixtures.QuoteRepairTests.setUp(self)
        plan = json.loads(self.cycle['plan'])
        plan['scope'] = scope.contract(self.cycle['track'], self.cycle['topic'])
        self.cycle['plan'] = json.dumps(plan)
        with self.store.db:
            self.store.db.execute('UPDATE demand_cycles SET plan=?', (self.cycle['plan'],))
        self.requests = [{**child('stock-check'), 'key': 'alternatives', 'source_ids': ['s2']},
                         {**child('returns-check'), 'key': 'alternatives', 'source_ids': ['s1'],
                          'trigger_quote': fixtures.ORIGINAL}]

    def events(self, event):
        return research._agent_events(self.store, 'cycle', event)

    def test_distinct_requests_survive_review_with_original_quotes_and_report(self):
        original = self.report(self.requests)
        cli = ContractCli()
        research._agent_review(self.store, self.cycle, cli)
        saved = agents.task(self.store.db, self.parent['id'])
        self.assertEqual(saved['status'], 'done')
        self.assertEqual(len(saved['result']['children']['admitted']), 2)
        actual = saved['result']['report']['child_requests']
        self.assertEqual(len({item['key'] for item in actual}), 2)
        for before, after in zip(self.requests, actual):
            self.assertEqual({k: v for k, v in before.items() if k != 'key'},
                             {k: v for k, v in after.items() if k != 'key'})
        self.assertEqual(self.events('role_report')[0]['report'], original)
        self.assertEqual(len(self.events('child_request_keys_bound')), 1)
        self.assertEqual(len(cli.calls), 2)  # independent scope and coordinator decisions

    def test_same_question_is_still_deduplicated_at_admission(self):
        self.requests[1] = copy.deepcopy(self.requests[0])
        self.report(self.requests)
        research._agent_review(self.store, self.cycle, ContractCli())
        result = agents.task(self.store.db, self.parent['id'])['result']['children']
        self.assertEqual(len(result['admitted']), 1)
        self.assertEqual(result['existing'], result['admitted'])

    def test_generated_labels_reserve_unique_inputs_and_fit_schema(self):
        keys = ['alternatives', 'alternatives', 'alternatives__request_2', 'x' * 100, 'x' * 100]
        proposals = [{**child('question-' + str(i)), 'key': key} for i, key in enumerate(keys)]
        before = copy.deepcopy(proposals)
        bound = scope._bind_child_keys(self.store, self.cycle, self.parent, proposals)
        self.assertEqual(proposals, before)
        actual = [item['key'] for item in bound]
        self.assertEqual(len(set(actual)), len(actual))
        self.assertEqual(actual[2], keys[2])
        self.assertEqual(actual[0], keys[0])
        self.assertTrue(all(3 <= len(key) <= 100 for key in actual))
        self.assertEqual(scope._bind_child_keys(self.store, self.cycle, self.parent, bound), bound)

    def test_scope_decisions_and_mapping_are_reused_after_restart(self):
        cli = ContractCli(reject_scope='alternatives__request_2')
        first = scope.guard_children(self.store, self.cycle, self.parent, self.requests, cli)
        self.assertEqual(len(first['requests']), 1)
        self.assertEqual(first['limitations'][0]['reason'], 'research_scope_cross_lane')
        self.reopen()
        second = scope.guard_children(self.store, self.cycle, self.parent, self.requests, cli)
        self.assertEqual(first, second)
        self.assertEqual(len(cli.calls), 1)
        self.assertEqual(len(self.events('child_request_keys_bound')), 1)

    def test_quote_repair_checkpoint_uses_same_binding_and_keeps_original_report(self):
        self.requests[1]['trigger_quote'] = fixtures.TRANSLATED
        original = self.report(self.requests)
        first = ContractCli()
        research._agent_review(self.store, self.cycle, first)
        self.assertEqual(len(first.calls), 2)
        self.reopen()
        second = ContractCli()
        research._agent_review(self.store, self.cycle, second)
        self.assertEqual(len(second.calls), 1)  # cached scope + citation; only coordinator needed
        result = agents.task(self.store.db, self.parent['id'])['result']
        self.assertEqual(len(result['children']['admitted']), 2)
        self.assertEqual(result['report']['child_requests'][1]['trigger_quote'], fixtures.ORIGINAL)
        self.assertEqual(self.events('role_report')[0]['report'], original)
        self.assertEqual(len(self.events('child_request_keys_bound')), 1)
        repairs = self.events('child_quote_repair')
        self.assertEqual([item['outcome'] for item in repairs], ['started', 'repaired'])
        self.assertTrue(all(item['key'] == 'alternatives__request_2' for item in repairs))

    def test_synthesis_binding_survives_quote_and_semantic_repair_checkpoints(self):
        self.requests[1]['trigger_quote'] = fixtures.TRANSLATED
        proposed = self.synthesis_fixture(self.requests)
        original = copy.deepcopy(proposed)
        first = ContractCli(synthesis=proposed)
        research._agent_synthesis(self.store, self.cycle, first)
        self.assertEqual(len(first.calls), 3)  # synthesis, scope, quote repair
        self.reopen()
        second = ContractCli()
        research._agent_synthesis(self.store, self.cycle, second)
        self.assertEqual(len(second.calls), 1)  # separate semantic review of repaired citation
        self.reopen()
        third = ContractCli()
        research._agent_synthesis(self.store, self.cycle, third)
        self.assertEqual(third.calls, [])
        snapshot = agents.snapshot(self.store.db, 'cycle')
        self.assertEqual(len([item for item in snapshot['tasks'] if item['parent_task_id']]), 2)
        actual = snapshot['syntheses'][0]['result']['child_requests']
        self.assertEqual(len({item['key'] for item in actual}), 2)
        self.assertEqual(actual[1]['trigger_quote'], fixtures.ORIGINAL)
        self.assertEqual(self.events('synthesis_proposed')[0]['result']['child_requests'], original['child_requests'])
        self.assertEqual(len(self.events('child_request_keys_bound')), 1)

    def test_unique_keys_create_no_binding_event_and_malformed_reviews_stay_strict(self):
        proposals = [{**self.requests[0], 'key': 'one'}, {**self.requests[1], 'key': 'two'}]
        self.assertEqual(scope._bind_child_keys(self.store, self.cycle, self.parent, proposals), proposals)
        self.assertEqual(self.events('child_request_keys_bound'), [])
        class Malformed(ContractCli):
            def structured(inner, prompt, schema):
                result = super().structured(prompt, schema)
                result['scope_decisions'][1] = result['scope_decisions'][0]
                return result
        with self.assertRaisesRegex(scope.ScopeError, 'research_scope_review_invalid'):
            scope.guard_children(self.store, self.cycle, self.parent, proposals, Malformed())

    def test_malformed_scope_format_diagnostic_does_not_claim_cross_lane(self):
        self.report(self.requests)
        with patch.object(scope, 'guard_children', side_effect=scope.ScopeError('research_scope_keys_invalid')):
            result = research.run_step('job', 'Research fixture', db_path=self.path,
                                       cards_dir=Path(self.directory.name) / 'cards', cli=ContractCli(), cycle_id='cycle')
        self.assertEqual(result['error_category'], 'research_scope_keys_invalid')
        self.assertIn('неверный формат', result['error'])
        self.assertNotIn('чужие поручения', result['error'])


if __name__ == '__main__':
    unittest.main()
