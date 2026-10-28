"""Regression: preserve a role's hypothesis identity and pre-cycle test contract."""
import copy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from iva_integration import demand_research as dr
import test_demand_research as fixtures


class HypothesisContext(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DemandResearch()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def prepared(self):
        cli = self.fixture.prepared()
        store = dr.Research(self.fixture.path, self.fixture.cards)
        self.addCleanup(store.close)
        cycle = store.rows('SELECT * FROM demand_cycles')[0]
        sources = {row['id']: row for row in store.rows('SELECT * FROM demand_sources')}
        proposal = copy.deepcopy(cli.analysis['hypotheses'][0])
        with store.db:
            saved = dr._save_hypothesis(store, cycle, proposal, sources)
        return store, cycle, sources, proposal, saved['id']

    def payload(self, store, identity):
        return json.loads(store.rows('SELECT payload FROM demand_hypotheses WHERE id=?', (identity,))[0]['payload'])

    def test_role_analysis_receives_identity_and_real_test_keys_without_other_role_hypothesis(self):
        store, cycle, sources, proposal, identity = self.prepared()
        mine = proposal['evidence'][0]['source_id']
        other = proposal['evidence'][-1]['source_id']
        separate = copy.deepcopy(proposal)
        separate.update(key='unrelated-hypothesis', buyer='OTHER_ROLE_BUYER_551', problem='OTHER_ROLE_PROBLEM_551')
        separate['evidence'] = [copy.deepcopy(proposal['evidence'][-1])]
        with store.db:
            dr._save_hypothesis(store, cycle, separate, sources)
        task = {'id':'task-buyer', 'role':'buyer_problem', 'parent_task_id':None, 'payload':{}}
        cli = fixtures.FakeCli(analysis={'hypotheses':[], 'gaps':[]})
        with patch.object(dr, '_agent_cycle', return_value=True), \
             patch.object(dr, '_agent_task_for_batch', return_value=task), \
             patch.object(dr, '_agent_sources', return_value={mine:sources[mine]}), \
             patch.object(store, 'advance', return_value='agent_report'):
            dr._analyze(store, cycle, cli)
        prompt = cli.calls[0]
        context = json.loads(prompt.split('Старые гипотезы: ',1)[1].split('\nФактический прогресс',1)[0])
        self.assertEqual([item['id'] for item in context], [identity])
        self.assertEqual(context[0]['buyer'], proposal['buyer'])
        self.assertEqual(context[0]['problem'], proposal['problem'])
        self.assertEqual(context[0]['next_test'], proposal['next_test'])
        self.assertIsNone(context[0]['previous_test'])
        self.assertEqual([item['source_id'] for item in context[0]['evidence']], [mine])
        self.assertNotIn(other, dr.dumps(context))
        self.assertNotIn('OTHER_ROLE_BUYER_551', dr.dumps(context))
        self.assertIn('pass_condition/fail_condition', prompt)
        self.assertIn('previous_test=null', prompt)
        self.assertNotIn('state', context[0])
        self.assertNotIn('unknowns', context[0])
        self.assertNotIn('counterarguments', context[0])

    def test_critic_after_own_chunk_does_not_receive_merged_other_role_conclusions(self):
        store, cycle, sources, proposal, identity = self.prepared()
        mine = proposal['evidence'][0]['source_id']
        merged = self.payload(store, identity)
        merged['counterarguments'].append('OTHER_ROLE_COUNTERARGUMENT_447')
        merged['unknowns'].append('OTHER_ROLE_UNKNOWN_447')
        with store.db:
            store.db.execute('UPDATE demand_hypotheses SET state=?,payload=? WHERE id=?', ('desk_supported',dr.dumps(merged),identity))
        task = {'id':'task-critic', 'role':'critic', 'parent_task_id':None, 'payload':{}}
        cli = fixtures.FakeCli(analysis={'hypotheses':[], 'gaps':[]})
        with patch.object(dr, '_agent_cycle', return_value=True), \
             patch.object(dr, '_agent_task_for_batch', return_value=task), \
             patch.object(dr, '_agent_sources', return_value={mine:sources[mine]}), \
             patch.object(store, 'advance', return_value='agent_report'):
            dr._analyze(store, cycle, cli)
        prompt = cli.calls[0]
        self.assertNotIn('OTHER_ROLE_COUNTERARGUMENT_447',prompt)
        self.assertNotIn('OTHER_ROLE_UNKNOWN_447',prompt)
        self.assertNotIn('desk_supported',prompt)
        context = json.loads(prompt.split('Старые гипотезы: ',1)[1].split('\nФактический прогресс',1)[0])
        self.assertEqual(context[0]['next_test'],proposal['next_test'])
        self.assertEqual([e['source_id'] for e in context[0]['evidence']],[mine])

    def test_new_card_cannot_claim_prior_test_supported_in_later_same_cycle_batch(self):
        store, cycle, sources, proposal, identity = self.prepared()
        for verdict in ('supported', 'contradicted', 'inconclusive'):
            with self.subTest(verdict=verdict):
                update = copy.deepcopy(proposal)
                update['existing_id'] = identity
                update['test_result'] = {'verdict':verdict, 'basis':'Claim from a later batch.', 'evidence_source_ids':[update['evidence'][0]['source_id']]}
                with store.db:
                    dr._save_hypothesis(store, cycle, update, sources)
                saved = self.payload(store, identity)
                self.assertIsNone(saved['previous_test'])
                self.assertEqual(saved['test_result']['verdict'], 'not_tested')
                self.assertEqual(saved['test_result']['evidence_source_ids'], [])

    def test_legacy_supported_without_previous_contract_is_not_resurrected(self):
        store, cycle, sources, proposal, identity = self.prepared()
        old = self.payload(store, identity)
        old['test_result'] = {'verdict':'supported', 'basis':'Historical incorrect same-cycle result.', 'evidence_source_ids':[proposal['evidence'][0]['source_id']]}
        with store.db:
            store.db.execute('UPDATE demand_hypotheses SET payload=? WHERE id=?', (dr.dumps(old),identity))
        update = copy.deepcopy(proposal);update['existing_id']=identity
        with store.db:
            dr._save_hypothesis(store, cycle, update, sources)
        self.assertEqual(self.payload(store, identity)['test_result']['verdict'], 'not_tested')

    def test_incomplete_prior_contract_fails_closed(self):
        store, cycle, sources, proposal, identity = self.prepared()
        old = self.payload(store, identity)
        old['previous_test'] = {'support_condition':'Not the hypothesis schema', 'refute_condition':'Wrong contract'}
        with store.db:
            store.db.execute('UPDATE demand_hypotheses SET payload=? WHERE id=?', (dr.dumps(old),identity))
        update = copy.deepcopy(proposal);update['existing_id']=identity
        update['test_result']={'verdict':'supported','basis':'A source exists but no frozen criterion.','evidence_source_ids':[proposal['evidence'][0]['source_id']]}
        with store.db:
            dr._save_hypothesis(store, cycle, update, sources)
        self.assertEqual(self.payload(store, identity)['test_result']['verdict'], 'not_tested')

    def test_next_cycle_preserves_prior_criterion_when_new_next_test_changes(self):
        cli = self.fixture.prepared()
        first = self.fixture.step(cli)
        identity = first['hypotheses'][0]['id']
        first_payload = json.loads(self.fixture.query('SELECT payload FROM demand_hypotheses')[0]['payload'])
        self.fixture.clock += timedelta(hours=1)
        cli = self.fixture.prepared(cli)
        store = dr.Research(self.fixture.path, self.fixture.cards)
        self.addCleanup(store.close)
        cycle = store.rows("SELECT * FROM demand_cycles WHERE state='running'")[0]
        sources = {row['id']:row for row in store.rows('SELECT * FROM demand_sources')}
        update = copy.deepcopy(cli.analysis['hypotheses'][0]);update['existing_id']=identity
        update['next_test']['pass_condition']='A new criterion for a future cycle, not this verdict.'
        update['test_result']={'verdict':'supported','basis':'Evidence addresses the previously saved criterion.','evidence_source_ids':[update['evidence'][0]['source_id']]}
        with store.db:
            dr._save_hypothesis(store, cycle, update, sources)
        saved = self.payload(store, identity)
        self.assertEqual(saved['previous_test'], first_payload['next_test'])
        self.assertEqual(saved['test_result']['verdict'], 'supported')
        context = dr._agent_evidence(store, cycle, {update['evidence'][0]['source_id']})[0]
        self.assertEqual(context['previous_test'], first_payload['next_test'])
        self.assertNotEqual(context['next_test'], context['previous_test'])

    def test_accumulated_notes_are_preserved_and_labeled_as_batch_history(self):
        store, cycle, sources, proposal, identity = self.prepared()
        old = self.payload(store, identity)
        stale = 'В единственном прямом форуме описана проблема.'
        old['counterarguments'].append(stale)
        old['unknowns'].append('Неизвестность из первого объёма источников.')
        with store.db:
            store.db.execute('UPDATE demand_hypotheses SET payload=? WHERE id=?', (dr.dumps(old),identity))
        update = copy.deepcopy(proposal);update['existing_id']=identity
        update['counterarguments']=['Теперь прочитаны другие независимые обсуждения.']
        with store.db:
            dr._save_hypothesis(store, cycle, update, sources)
        store.export_cards(cycle['job_id'])
        card = (store.cards/(identity+'.md')).read_text(encoding='utf-8')
        self.assertIn(stale,card)
        self.assertIn('Теперь прочитаны другие независимые обсуждения.',card)
        self.assertIn('Накопленные замечания исследовательских партий',card)
        self.assertIn('не являются перечнем окончательно актуальных ограничений',card)
        self.assertIn('Накопленные неизвестные параметры',card)
        context = dr._agent_evidence(store, cycle, {proposal['evidence'][0]['source_id']})[0]
        self.assertIn('accumulated notes', context['notes_semantics'])


if __name__=='__main__':unittest.main()
