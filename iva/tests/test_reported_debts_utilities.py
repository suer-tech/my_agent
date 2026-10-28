from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Ledger


class ReportedPlanningTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:')
        self.account=self.l.account('Test','100000.00','2026-10-18')['id']
        self.l.configure('0','0',self.account)

    def tearDown(self):self.l.db.close()

    def debt(self,**updates):
        values=dict(debt_ref='separate',name='Same bank separate loan',monthly_payment='230.00',due_day=16,planning_from_period='2026-10',reported_as_of='2026-10-18',source='report1',notes='Reported only',account=self.account)
        values.update(updates)
        return self.l.debt_save(**values)['id']

    def bill(self,**updates):
        values=dict(bill_ref='electricity',name='Electricity',start_period='2026-10',due_day=20,account=self.account,policy={'baseline':'80','variable':True,'overall_range':['20','110'],'cold_typical_range':['70','80'],'cold_severe_range':['100','110'],'warm_range':['20','40'],'season_boundaries_known':False},notes='Season boundaries unknown; baseline is an estimate',source='reported')
        values.update(updates)
        return self.l.utility_save(**values)['id']

    def test_external_paid_closes_period_without_cash_or_fabricated_history(self):
        key=self.debt()
        self.l.debt_status(key,'2026-10','external_paid','owner')
        events=self.l.calendar('2026-10-18','2026-11-30')
        self.assertEqual([e['date'] for e in events],['2026-11-16'])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])
        self.assertEqual(self.l.rows('SELECT * FROM reported_loan_periods')[0]['payment_date'],None)
        self.l.queue_reminders('2026-10-18','2026-11-30')
        self.assertEqual(self.l.rows('SELECT due FROM reminders')[0]['due'],'2026-11-15')

    def test_unknown_past_execution_is_uncertain_without_debit_or_reminder(self):
        key=self.debt()
        self.l.debt_status(key,'2026-10','unknown','owner')
        result=self.l.forecast('2026-10-18','2026-10-31')
        self.assertFalse(result['affordable'])
        self.assertEqual(len(result['uncertain_obligations']),1)
        self.assertEqual(result['minimum'],10000000)
        self.assertEqual(self.l.queue_reminders('2026-10-18','2026-10-31'),[])

    def test_approximate_term_is_never_returned_as_principal(self):
        key=self.debt(issued_month='2024-07',term_months=60)
        self.l.debt_status(key,'2026-10','external_paid','owner')
        result=self.l.debt_term_info(key,'2026-10-18')
        self.assertEqual(result['remaining_calendar_months_approx'],33)
        self.assertEqual(result['scenario_future_payment_count'],33)
        self.assertEqual(result['scenario_future_payments_total'],23000*33)
        self.assertIsNone(result['principal_balance'])
        self.assertIsNone(result['exact_contract_last_due_date'])

    def test_total_correction_preserves_one_entity_and_both_reports(self):
        key=self.debt(reported_total_balance='1000',payment_is_estimate=True)
        corrected=self.debt(reported_total_balance='1001.23',payment_is_estimate=True,source='report2')
        self.assertEqual(key,corrected)
        self.assertEqual(len(self.l.rows('SELECT * FROM reported_loans')),1)
        self.assertEqual(len(self.l.rows('SELECT * FROM reported_loan_reports')),2)
        self.assertEqual(self.l.rows('SELECT * FROM reported_loans')[0]['reported_total_balance'],100123)
        self.assertTrue(self.l.calendar('2026-11-01','2026-11-30')[0]['amount_is_estimate'])

    def test_same_bank_card_rule_not_cancelled_by_distinct_loan(self):
        card=self.l.schedule('Same bank','expense','55',self.account,monthday=28,start='2026-10-01')['id']
        loan=self.l.schedule('Same bank','expense','230',self.account,monthday=16,start='2026-10-01')['id']
        self.debt(source_line_name='Same bank',source_line_amount='230')
        rows={r['id']:r for r in self.l.rows('SELECT * FROM rules')}
        self.assertIsNone(rows[card]['end'])
        self.assertEqual(rows[loan]['end'],'2026-10-18')

    def test_known_month_overrides_variable_estimate_and_invoice_replaces_it(self):
        key=self.bill();self.l.utility_quote(key,'2026-10','50','october')
        events=self.l.calendar('2026-10-18','2026-11-30')
        self.assertEqual([e['amount'] for e in events],[5000,8000])
        self.assertFalse(events[0]['amount_is_estimate']);self.assertTrue(events[1]['amount_is_estimate'])
        self.assertIsNone(events[1]['season_assignment'])
        self.l.utility_quote(key,'2026-11','72.34','invoice')
        self.l.utility_quote(key,'2026-11','72.34','invoice')
        events=self.l.calendar('2026-11-01','2026-11-30')
        self.assertEqual(len(events),1);self.assertEqual(events[0]['amount'],7234)
        self.assertFalse(events[0]['amount_is_estimate'])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_internet_past_unknown_not_overdue_november_reminder_tenth(self):
        key=self.bill(bill_ref='internet',name='Internet',due_day=11,policy={'baseline':'11.05','variable':False})
        self.l.utility_status(key,'2026-10','unknown','owner')
        events=self.l.calendar('2026-10-18','2026-11-30')
        self.assertEqual(len(events),2);self.assertEqual(events[0]['period_status'],'unknown')
        result=self.l.forecast('2026-10-18','2026-10-31')
        self.assertFalse(result['affordable']);self.assertEqual(result['minimum'],10000000)
        self.l.queue_reminders('2026-10-18','2026-11-30')
        self.assertEqual(self.l.rows('SELECT due FROM reminders'),[{'due':'2026-11-10'}])

    def test_bill_resave_and_external_paid_keep_single_event(self):
        key=self.bill();self.assertEqual(key,self.bill())
        self.l.utility_status(key,'2026-10','external_paid','owner')
        self.assertEqual(len(self.l.calendar('2026-10-18','2026-11-30')),1)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])


if __name__=='__main__':unittest.main()
