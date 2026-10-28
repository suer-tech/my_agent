from pathlib import Path
import json
import re
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Ledger,Assistant
from local_finance.bot import OWNER
from finance.planning import income_events,expense_events


class FamilyPlanningTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:')
        self.a=self.l.family_opening('0','2026-10-19','owner:zero')['account']
        self.income('salary20',20,reference_amount='120000',estimate_note='Обычно меньше, зависит от рабочих дней прошлого завершённого месяца',earning_month_rule='previous_completed_month',delay_risk=True)
        self.income('salary10',10,weekend_rule='previous_friday',income_group='pair')
        self.income('salary25',25,weekend_rule='previous_friday',income_group='pair')
        self.l.income_group_save('pair','10 и 25 вместе','30000',['salary10','salary25'],'group:owner','Распределение неизвестно')

    def income(self,ref,monthday,**kw):
        return self.l.income_save(ref,ref,monthday,'2026-10-19','source:'+ref,account=self.a,**kw)

    def tearDown(self):self.l.db.close()

    def forecast(self,**kw):return self.l.family_forecast('2026-10-19','2026-11-30',**kw)

    def expense(self,ref='food',amount='30000',**kw):
        return self.l.family_expense_save(ref,ref,'Еда',amount,'2026-10','list:'+ref,**kw)

    def test_zero_is_known_not_missing_and_repeated_opening_is_idempotent(self):
        self.assertTrue(self.l.family_opening('0','2026-10-19','owner:zero')['duplicate'])
        r=self.forecast();self.assertEqual(r['actual_balance'],0);self.assertNotIn('opening_balance',r['missing'])
        self.assertFalse(self.l.rows('SELECT * FROM operations'))
        self.assertEqual(len(self.l.rows('SELECT * FROM accounts')),1)

    def test_weekends_tenth_and_twenty_fifth_only(self):
        events=income_events(self.l,'2026-10-19','2027-02-28')
        by={(e['ref'],e['nominal_date']):e['date'] for e in events}
        self.assertEqual(by['salary25','2026-10-25'],'2026-10-23')
        self.assertEqual(by['salary10','2027-01-10'],'2027-01-08')
        self.assertEqual(by['salary20','2026-12-20'],'2026-12-20')
        self.assertEqual(by['salary25','2026-12-25'],'2026-12-25') # Holidays not inferred.

    def test_no_invented_split_or_past_tenth_income(self):
        r=self.forecast();self.assertEqual(r['monthly_income_reference']['full_nominal_month_estimate'],15000000)
        self.assertIsNone(r['monthly_income_reference']['remaining_current_month'])
        self.assertTrue(all(e['amount'] is None for e in r['expected_income']))
        self.assertTrue(all(e['date']>='2026-10-19' for e in r['expected_income']))
        self.assertFalse(r['complete']);self.assertEqual(r['available_for_early_repayment'],0)
        self.assertTrue(all(d['known_flow_balance']==0 for d in r['days']))

    def test_previous_completed_earning_month_not_current_and_no_daily_formula(self):
        e=next(e for e in income_events(self.l,'2026-10-19','2026-10-31') if e['ref']=='salary20')
        self.assertEqual(e['earning_month'],'2026-09');self.assertIsNone(e['amount'])
        self.assertEqual(e['reference_amount'],12000000)

    def test_delay_without_date_excludes_credit_without_inventing_limit(self):
        r=self.l.family_scenarios('2026-10-19','2026-10-31',income_amounts={'salary20':'100000'})
        self.assertEqual(r['on_time']['days'][1]['known_flow_balance'],10000000)
        self.assertTrue(all(d['known_flow_balance']==0 for d in r['delayed']['days']))
        self.assertEqual(r['on_time']['actual_balance'],0)

    def test_explicit_delay_changes_only_one_occurrence(self):
        r=self.l.family_scenarios('2026-10-19','2026-11-30',delay20_until='2026-10-24',income_amounts={'salary20':'100000'})
        delayed=r['delayed']['days']
        self.assertEqual(delayed[1]['known_flow_balance'],0)
        self.assertEqual(delayed[5]['known_flow_balance'],10000000)
        self.assertEqual(next(e['date'] for e in r['delayed']['expected_income'] if e['nominal_date']=='2026-11-20'),'2026-11-20')

    def test_receipt_actual_confirmed_idempotent_and_expectation_not_doubled(self):
        data=dict(ref='salary20',nominal_date='2026-10-20',amount='111000',on_date='2026-10-22',source='actual:salary20')
        r=self.l.income_receive(**data);self.assertEqual(r['earning_month'],'2026-09')
        self.assertTrue(self.l.income_receive(**data)['duplicate'])
        forecast=self.l.family_forecast('2026-10-22','2026-10-31')
        self.assertEqual(forecast['actual_balance'],11100000)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
        self.assertFalse(any(e['ref']=='salary20' for e in forecast['expected_income']))
        with self.assertRaises(ValueError):self.l.income_receive(**{**data,'amount':'120000'})

    def test_owner_proposal_is_not_money_before_confirmation(self):
        assistant=Assistant(self.l,None)
        p=dict(ref='salary20',nominal_date='2026-10-20',amount='111000',on_date='2026-10-22',source='preview:receipt')
        assistant.preview('income_receive',p)
        self.assertFalse(self.l.rows('SELECT * FROM operations'))

    def test_receipt_owner_confirmation_and_repeat_confirmation(self):
        assistant=Assistant(self.l,None)
        p=dict(ref='salary20',nominal_date='2026-10-20',amount='111000',on_date='2026-10-22',source='owner-confirm:receipt')
        preview=assistant.propose(OWNER,'income_receive',p,'owner-request:receipt')
        key=re.search(r'/confirm ([a-f0-9]+)',preview).group(1)
        self.assertFalse(self.l.rows('SELECT * FROM operations'))
        reply=assistant.handle(OWNER,'/confirm '+key,'owner-confirm:1')
        self.assertIn('Выполнено',reply)
        assistant.handle(OWNER,'/confirm '+key,'owner-confirm:2')
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
        self.assertEqual(self.l.balances('2026-10-22')[self.a],11100000)

    def test_unknown_remaining_and_future_month_hold_not_fake_debit(self):
        self.expense();r=self.forecast()
        self.assertTrue(r['uncertain_expenses'])
        self.assertEqual(r['actual_balance'],0)
        self.assertEqual(r['minimum_conservative'],-6000000)
        self.assertTrue(any(e.get('budget_hold') for d in r['days'] for e in d['events']))
        self.assertFalse(self.l.rows('SELECT * FROM operations'))

    def test_unknown_identity_not_seventh_debt_or_added_as_duplicate(self):
        self.expense('unresolved-sovkom','2500',uncertain_identity=True)
        r=self.forecast();self.assertEqual(r['minimum_conservative'],0)
        self.assertTrue(r['uncertain_expenses'][0]['uncertain_identity'])

    def test_separate_loan_identity_resolved_one_envelope_unknown_date(self):
        self.l.family_expense_save('small-loan','Separate loan','Кредиты','2500','2026-10','original:small-loan',uncertain_identity=True)
        data=dict(ref='separate-loan',name='Separate loan',expense_ref='small-loan',reported_as_of='2026-10-19',source='owner:separate-loan',notes='Separate credit, details later')
        self.l.family_debt_save(**data)
        self.assertTrue(self.l.family_debt_save(**data)['duplicate'])
        inventory=self.l.debt_inventory('2026-10-19')['debts']
        self.assertEqual(len(inventory),1);self.assertIsNone(inventory[0]['current_principal']);self.assertIsNone(inventory[0]['due_day'])
        r=self.l.family_forecast('2026-10-19','2026-10-31')
        events=[e for d in r['days'] for e in d['events'] if e.get('ref')=='small-loan']
        self.assertEqual(len(events),1);self.assertEqual(events[0]['amount'],250000)
        self.assertTrue(events[0]['budget_hold']);self.assertEqual(events[0]['status'],'unknown')
        self.assertFalse(events[0]['uncertain_identity']);self.assertFalse(self.l.rows('SELECT * FROM operations'))
        self.assertEqual(self.l.queue_reminders('2026-10-19','2026-11-30'),[])

    def test_separate_loan_cannot_silently_rebind_original_line(self):
        self.expense('one','2500');self.expense('two','2500')
        self.l.family_debt_save('loan','Loan','one','2026-10-19','owner:loan','Details unknown')
        with self.assertRaises(ValueError):self.l.family_debt_save('loan','Loan','two','2026-10-19','owner:loan2','Other line')

    def test_partial_linked_spending_decreases_envelope_once_and_void_restores(self):
        self.expense();self.l.family_expense_status('food','2026-10','remaining','owner:remaining','10000')
        d=self.l.stage({'kind':'expense','amount':'2000','date':'2026-10-20','account':self.a,'budget_ref':'food'},'receipt:food')
        op=self.l.confirm(d['id'],True)
        event=expense_events(self.l,'2026-10-20','2026-10-31')[0]
        self.assertEqual(event['amount'],800000)
        r=self.l.family_forecast('2026-10-20','2026-10-31')
        self.assertEqual(r['actual_balance'],-200000)
        self.assertEqual(r['minimum_conservative'],-1000000)
        self.l.void(op['id'],'test correction')
        self.assertEqual(expense_events(self.l,'2026-10-20','2026-10-31')[0]['amount'],1000000)

    def test_reserve_and_unknowns_block_prepaid_strategy(self):
        r=self.l.family_allocation('2026-10-19','2026-11-30')
        self.assertEqual(r['cash_available'],0);self.assertEqual(r['allocation_status'],'blocked_incomplete_budget')
        self.assertIsNone(r['optimal_strategy']);self.assertIsNone(r['interest_savings'])

    def test_daily_budget_cannot_duplicate_food(self):
        self.expense()
        with self.assertRaises(ValueError):self.forecast(daily_budget='100')

    def test_unknown_expense_amount_is_not_silently_zero(self):
        self.expense(amount=None);r=self.forecast()
        self.assertFalse(r['complete']);self.assertTrue(r['uncertain_expenses'])

    def test_new_family_tables_survive_snapshot_copy(self):
        copy=Ledger(':memory:')
        try:
            self.l.db.backup(copy.db)
            self.assertEqual(copy.family_forecast('2026-10-19','2026-10-31')['actual_balance'],0)
        finally:copy.db.close()

    def test_bank_sources_overdue_paid_link_and_unknown_execution_persist(self):
        payload={'currency':'RUB','questions':[],'pages_covered':[1],'rows':[
            {'date':'2026-10-17','payment':'200','page':1,'row_ref':'unpaid','unpaid_confirmed':True},
            {'date':'2026-10-19','payment':'50','page':1,'row_ref':'unknown'},
            {'date':'2026-11-24','payment':'100','page':1,'row_ref':'future'}]}
        with self.l.transaction():self.l.db.execute('INSERT INTO source_documents VALUES(?,?,?,?,?,?)',('doc','synthetic.bin','loan_schedule',1,json.dumps(payload),'extracted'))
        self.l.bank_import('doc','Synthetic bank')
        r=self.forecast()
        unpaid=[e for d in r['days'] for e in d['events'] if e.get('unpaid_confirmed')]
        self.assertEqual(len(unpaid),1);self.assertEqual(unpaid[0]['date'],'2026-10-19')
        self.assertEqual(r['days'][0]['known_flow_balance'],-20000)
        later=self.l.family_forecast('2026-11-01','2026-11-30')
        self.assertTrue(any(e.get('original_date')=='2026-10-19' for e in later['uncertain_expenses']))
        event=unpaid[0]
        draft=self.l.stage({'kind':'expense','amount':'200','date':'2026-10-20','account':self.a,'occurrence':event['occurrence']},'bank:paid')
        self.l.confirm(draft['id'],True)
        paid=self.l.family_forecast('2026-10-20','2026-10-31')
        self.assertEqual(paid['actual_balance'],-20000)
        self.assertFalse(any(e.get('unpaid_confirmed') for d in paid['days'] for e in d['events']))
        self.assertIsNone(self.l.debt_inventory('2026-10-19')['debts'][0]['current_principal'])

    def test_confirmed_residual_unpaid_carries_to_next_month_once(self):
        self.expense('school','500')
        self.l.family_expense_status('school','2026-10','unpaid','owner:unpaid')
        events=expense_events(self.l,'2026-11-01','2026-11-30')
        self.assertEqual(len(events),2)
        self.assertEqual(sum(e['amount'] for e in events),100000)

    def test_reserve_detects_gap_even_without_any_events(self):
        r=self.l.family_forecast('2026-10-19','2026-10-19',reserve='100')
        self.assertEqual(r['first_gap'],'2026-10-19')

if __name__=='__main__':unittest.main()
