"""Demand estimates are scenarios; all mutation tests use in-memory ledgers."""
import calendar
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Ledger, Assistant
from finance.planning import expense_events


class RemainingEstimatesTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:')
        self.account=self.l.family_opening('100000','2024-01-01','fixture:opening')['account']

    def tearDown(self):self.l.db.close()

    def expense(self,ref='food',amount='30000',method='calendar_daily',**kw):
        self.l.family_expense_save(ref,ref,'fixture',amount,'2024-01','fixture:'+ref,**kw)
        if method:self.l.family_expense_policy(ref,method,'fixture:confirmed-pattern:'+ref)

    def events(self,start='2026-10-19',end='2026-10-31'):
        return expense_events(self.l,start,end)

    def spend(self,amount,when,ref='food',source='purchase'):
        draft=self.l.stage({'kind':'expense','amount':amount,'date':when,'account':self.account,'note':'fixture','budget_ref':ref,'ambiguities':[]},source)
        return self.l.confirm(draft['id'],True)

    def test_real_month_lengths_including_leap_and_rounding(self):
        self.expense(amount='100')
        for start,end,denom,numerator in [('2024-02-19','2024-02-29',29,11),('2026-02-19','2026-02-28',28,10),('2026-04-19','2026-04-30',30,12),('2026-10-19','2026-10-31',31,13)]:
            with self.subTest(start=start):
                e=self.events(start,end)[0];self.assertEqual(e['amount'],(10000*numerator*2+denom)//(2*denom))
                self.assertEqual(e['estimate']['days_in_month'],denom)
                self.assertTrue(e['estimate']['includes_current_day'])
                self.assertEqual(e['estimate']['remaining_calendar_days'],numerator)
        self.assertFalse(self.l.rows('SELECT * FROM operations'))

    def test_owner_override_never_decays_daily_and_partial_facts_only(self):
        self.expense();self.spend('1000','2026-10-18',source='prior')
        source=json.dumps({'source':'owner','confidence':'estimated','reported_as_of':'2026-10-19','expires_with_period':True})
        self.l.family_expense_status('food','2026-10','remaining',source,'10000')
        self.assertEqual(self.events()[0]['amount'],1000000)
        self.assertEqual(self.events('2026-10-25')[0]['amount'],1000000)
        self.spend('1200','2026-10-20')
        self.assertEqual(self.events('2026-10-25')[0]['amount'],880000)
        self.l.family_expense_status('food','2026-10','remaining',source,'10000')
        self.assertEqual(self.events('2026-10-25')[0]['amount'],880000)
        self.assertIn('120000',self.events()[0]['estimate']['formula'])

    def test_past_facts_not_subtracted_from_remaining_days(self):
        self.expense();before=self.events()[0]['amount']
        self.spend('20000','2026-10-18')
        self.assertEqual(self.events()[0]['amount'],before)
        self.assertEqual(self.l.balances('2026-10-19')[self.account],8000000)

    def test_future_linked_fact_replaces_overlapping_demand_once(self):
        self.expense();gross=self.events()[0]['amount']
        self.spend('1000','2026-10-21')
        event=self.events()[0];self.assertEqual(event['amount'],gross-100000)
        r=self.l.family_forecast('2026-10-19','2026-10-31')
        self.assertEqual(r['remaining_needs']['total_allocated'],gross)
        self.assertEqual(r['days'][-1]['conservative_allocated_balance'],10000000-gross)

    def test_same_day_fact_already_in_cash_removed_from_demand(self):
        self.expense();gross=self.events()[0]['amount'];self.spend('1000','2026-10-19')
        r=self.l.family_forecast('2026-10-19','2026-10-31')
        self.assertEqual(r['actual_balance'],9900000)
        self.assertEqual(r['remaining_needs']['total_allocated'],gross-100000)
        self.assertEqual(r['days'][-1]['conservative_allocated_balance'],10000000-gross)

    def test_future_full_month_uses_monthly_plan_and_estimate_expires(self):
        self.expense()
        self.l.family_expense_status('food','2026-10','remaining',json.dumps({'confidence':'estimated','expires_with_period':True}),'10000')
        es=self.events('2026-11-01','2026-11-30');self.assertEqual(len(es),1)
        self.assertEqual(es[0]['amount'],3000000);self.assertIsNone(es[0]['estimate'])
        both=self.events('2026-10-19','2026-11-30')
        self.assertEqual([e['amount'] for e in both],[1000000,3000000])

    def test_child_food_and_unknown_classes_are_not_prorated(self):
        self.expense('child_food','4000',method='monthly_only')
        self.expense('kindergarten','5000',method=None)
        self.expense('class','3500',method=None)
        source=json.dumps({'confidence':'estimated','condition':{'kind':'after_actual_income','ref':'salary20'}})
        self.l.family_expense_status('child_food','2026-10','remaining',source,'4000')
        es={e['ref']:e for e in self.events()}
        self.assertEqual(es['child_food']['amount'],400000)
        self.assertEqual(es['child_food']['remaining_provenance']['condition']['ref'],'salary20')
        self.assertTrue(es['child_food']['undated'])
        self.assertEqual(es['kindergarten']['amount'],500000);self.assertIsNone(es['kindergarten']['estimate'])
        self.assertEqual(es['class']['amount'],350000)
        with self.assertRaises(ValueError):
            self.l.family_expense_save('fixed','fixed','fixture','100','2024-01','fixed',due_day=22)
            self.l.family_expense_policy('fixed','calendar_daily','wrong')

    def test_bus_graph_has_priority_over_monthly_ratio(self):
        self.expense('bus','1800',method=None)
        self.l.family_expense_policy('bus','weekly_travel','owner:bus',weekdays=[2,4],daily_cost='224')
        e=self.events()[0]
        self.assertEqual(e['amount'],89600)
        self.assertEqual(e['estimate']['travel_dates'],['2026-10-21','2026-10-23','2026-10-28','2026-10-30'])
        self.assertEqual(self.events('2026-10-22')[0]['amount'],67200)
        self.l.family_expense_status('bus','2026-10','remaining','owner:explicit','700')
        self.assertEqual(self.events()[0]['amount'],70000)

    def test_unclassified_bus_uses_no_name_guess(self):
        self.expense('bus','1800',method=None)
        self.assertEqual(self.events()[0]['amount'],180000)
        self.l.family_expense_policy('bus','calendar_daily','owner:uniform')
        self.assertEqual(self.events()[0]['amount'],75484)

    def test_default_local_date_and_explicit_date_override_are_read_only(self):
        self.expense();now=datetime.now(timezone(timedelta(hours=5))).date()
        r=self.l.family_forecast()
        self.assertEqual(r['as_of'],now.isoformat());self.assertEqual(r['until'],now.replace(day=calendar.monthrange(now.year,now.month)[1]).isoformat())
        r=self.l.family_forecast(as_of='2024-02-19')
        self.assertEqual(r['until'],'2024-02-29')
        self.assertFalse(self.l.rows('SELECT * FROM operations'));self.assertFalse(self.l.rows('SELECT * FROM family_expense_periods'))

    def test_as_of_before_owner_report_does_not_use_future_remaining(self):
        self.expense()
        self.l.family_expense_status('food','2026-10','remaining',json.dumps({'reported_as_of':'2026-10-25'}),'100')
        self.assertEqual(self.events()[0]['amount'],1258065)

    def test_policy_preview_cannot_mutate_live_pattern(self):
        self.expense(method=None)
        Assistant(self.l,None).preview('family_expense_policy',{'ref':'food','method':'calendar_daily','source':'owner:pattern'})
        self.assertFalse(self.l.rows('SELECT * FROM family_expense_policies'))


if __name__=='__main__':unittest.main()
