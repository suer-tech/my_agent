import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import OWNER
from local_finance.attachments import normalize_display_amount,normalize_extraction,merge_overlap


class StubCLI:
    def plan(self,prompt):return 'Ответ','none',{}


class DecimalPolicyTests(unittest.TestCase):
    def test_complete_zero_one_two_decimals_right_pad_only(self):
        samples={'123':'123.00','123,':'123.00','123,4':'123.40','123,45':'123.45','123.4':'123.40','1 234,5 ₽':'1234.50','1\u202f234,5':'1234.50','0,1':'0.10','0,01':'0.01'}
        for raw,expected in samples.items():
            with self.subTest(raw=raw):self.assertEqual(normalize_display_amount(raw),expected)

    def test_no_borrowed_kopeck_digit_and_no_float_or_rounding(self):
        data=normalize_extraction({'rows':[{'payment_display':'123,4','payment':'123.45'},{'payment_display':'123,','payment':None},{'payment_display':'123,45','payment':'123.45'}]})
        self.assertEqual([r['payment'] for r in data['rows']],['123.40','123.00','123.45'])
        self.assertEqual(data['rows'][0]['payment_display'],'123,4')
        for invalid in ('123,456','12?','1e2',0.1):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):normalize_display_amount(invalid)

    def test_equal_normalized_overlap_is_not_conflict(self):
        rows=[{'payment_no':5,'date':'2026-10-20','payment_display':'123,4','payment':None,'page':1,'row_ref':'p1'},
              {'payment_no':5,'date':'2026-10-20','payment_display':'123.40','payment':'123.40','page':2,'row_ref':'p2'}]
        merged=merge_overlap(normalize_extraction({'rows':rows,'questions':[]}))
        self.assertEqual(len(merged['rows']),1);self.assertEqual(merged['questions'],[])
        self.assertEqual(merged['rows'][0]['payment'],'123.40')


class FinancialRegressionTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');self.a=self.l.account('Карта','1000.00','2026-10-18')['id'];self.l.configure('0.00','0.00')
        self.assistant=Assistant(self.l,StubCLI())
    def tearDown(self):self.l.db.close()
    def document(self,key='doc',rows=None):
        if rows is None:
            rows=[{'date':'2026-05-17','payment':'20.00','page':1,'row_ref':'historical-not-confirmed-unpaid'},
                  {'date':'2026-10-17','payment':'123.40','page':1,'row_ref':'overview|user-due-date','unpaid_confirmed':True},
                  {'date':'2026-11-24','payment':'123.45','page':1,'row_ref':'future'}]
        payload={'currency':'RUB','questions':[],'pages_covered':[1],'rows':rows}
        with self.l.transaction():self.l.db.execute('INSERT INTO source_documents VALUES(?,?,?,?,?,?)',(key,'synthetic-private.bin','loan_schedule',1,json.dumps(payload),'extracted'))
        return key

    def test_explicit_overdue_before_starting_balance_carries_into_october_once(self):
        self.l.bank_import(self.document(),'Банк',self.a)
        forecast=self.l.forecast('2026-10-18','2026-10-31')
        self.assertEqual(forecast['days'][0]['total'],87660)
        self.assertEqual(forecast['days'][-1]['total'],87660)
        self.assertEqual(len(forecast['overdue_unsettled']),1)
        self.assertEqual(forecast['overdue_unsettled'][0]['date'],'2026-10-17')
        self.assertEqual(forecast['overdue_unsettled'][0]['days_overdue'],1)
        self.assertEqual(self.l.balances('2026-10-31')[self.a],100000)
        self.assertEqual(self.l.snapshot()['operations'],[])

    def test_overdue_days_are_local_calendar_computation_not_frozen_one(self):
        self.l.bank_import(self.document(),'Банк',self.a)
        with patch('finance.core.local_today',return_value='2026-10-18'):
            calendar=self.l.calendar('2026-10-18','2026-10-31');self.assertEqual(calendar[0]['days_overdue'],1)
        with patch('finance.core.local_today',return_value='2026-10-19'):
            self.assertEqual(self.l.calendar('2026-10-19','2026-10-31')[0]['days_overdue'],2)
        self.assertEqual(self.l.forecast('2026-10-19','2026-10-31')['overdue_unsettled'][0]['days_overdue'],2)

    def test_old_history_not_all_marked_overdue_and_debt_not_lost_next_month(self):
        self.l.bank_import(self.document(),'Банк',self.a)
        calendar=self.l.calendar('2026-11-18','2026-11-30')
        self.assertEqual([e['date'] for e in calendar],['2026-10-17','2026-11-24'])
        forecast=self.l.forecast('2026-11-18','2026-11-30')
        self.assertEqual(forecast['days'][-1]['total'],75315)
        self.assertEqual(len(forecast['overdue_unsettled']),1)

    def test_linked_actual_payment_clears_overdue_without_double_count(self):
        self.l.bank_import(self.document(),'Банк',self.a)
        event=self.l.calendar('2026-10-18','2026-10-31')[0]
        self.l.queue_reminders('2026-10-18','2026-10-31')
        d=self.l.stage({'kind':'expense','amount':'123.40','date':'2026-10-20','account':self.a,'occurrence':event['occurrence']},'synthetic:actual-payment')
        self.l.confirm(d['id'],True)
        self.assertEqual(self.l.forecast('2026-10-20','2026-10-31')['days'][-1]['total'],87660)
        self.assertEqual(self.l.forecast('2026-10-20','2026-10-31')['overdue_unsettled'],[])
        self.assertEqual(self.l.calendar('2026-10-20','2026-10-31'),[])
        self.l.queue_reminders('2026-10-20','2026-10-31')
        self.assertEqual(self.l.rows('SELECT state FROM reminders WHERE id=?',(event['occurrence'],))[0]['state'],'cancelled')

    def test_correcting_second_bank_review_keeps_first_pending(self):
        first=self.document('alpha',[{'date':'2026-11-20','payment':'100.00','page':1,'row_ref':'a'}])
        second=self.document('sovkom',[{'date':'2026-11-24','payment':'123.40','page':1,'row_ref':'s'}])
        self.assistant.propose(OWNER,'bank_import',{'document_id':first,'loan_name':'Первый банк','account':self.a},'doc:alpha')
        self.assistant.propose(OWNER,'bank_import',{'document_id':second,'loan_name':'Второй банк','account':self.a},'doc:sovkom')
        self.assertEqual(len(self.l.rows("SELECT id FROM local_pending WHERE state='pending'")),2)
        pending=self.assistant.handle(OWNER,'/pending','synthetic:pending')
        self.assertEqual(pending.count('/confirm '),2)

    def save_card(self):
        return self.l.card_save('Сбер','1000.00','52.17','2026-10-18','user:card-v1','Примерно шесть лет платит только проценты, это заметка, история неизвестна',due_day=28,first_due='2026-10-28',account=self.a)['id']

    def test_interest_only_no_amortization_no_fake_payoff_or_history(self):
        key=self.save_card();result=self.l.card_scenario(key,months=12)
        self.assertEqual(result['ending_balance'],100000);self.assertEqual(result['principal_paid'],0)
        self.assertIsNone(result['payoff_months']);self.assertIsNone(result['annual_rate']);self.assertIsNone(result['historical_interest_total'])
        self.assertEqual(self.l.snapshot()['operations'],[])
        self.assertEqual(self.l.snapshot()['loans'],[])
        self.assertEqual(len(self.l.rows('SELECT * FROM credit_card_reports')),1)

    def test_extra_principal_is_separate_conditional_scenario_not_real_payment(self):
        key=self.save_card();result=self.l.card_scenario(key,extra_principal='100.00',months=3)
        self.assertEqual(result['ending_balance'],70000);self.assertEqual(result['principal_paid'],30000)
        self.assertEqual(result['payoff_months'],10)
        self.assertEqual(self.l.rows('SELECT balance FROM credit_cards')[0]['balance'],100000)
        self.assertEqual(self.l.snapshot()['operations'],[])

    def test_card_monthly_28_replaces_approximate_and_single_october_plan(self):
        self.l.schedule('Сбер','expense','55.00',self.a,date='2026-10-28')
        self.l.schedule('Сбер','expense','55.00',self.a,monthday=28,start='2026-10-19')
        key=self.save_card();self.save_card()
        calendar=self.l.calendar('2026-10-18','2026-11-30')
        self.assertEqual([e['date'] for e in calendar],['2026-10-28','2026-11-28'])
        self.assertEqual([e['amount'] for e in calendar],[5217,5217])
        self.assertEqual(len(self.l.rows('SELECT * FROM credit_cards')),1)
        self.assertEqual(self.l.forecast('2026-10-18','2026-10-31')['days'][-1]['total'],94783)
        self.l.queue_reminders('2026-10-18','2026-10-31')
        self.assertEqual(self.l.rows('SELECT due FROM reminders')[0]['due'],'2026-10-27')
        with self.assertRaises(ValueError):self.l.schedule('Сбер','expense','55.00',self.a,monthday=28,start='2026-10-19')

    def test_actual_interest_payment_changes_cash_not_card_principal(self):
        key=self.save_card();event=self.l.calendar('2026-10-18','2026-10-31')[0]
        d=self.l.stage({'kind':'expense','amount':'52.17','date':'2026-10-28','account':self.a,'occurrence':event['occurrence']},'synthetic:interest-paid')
        self.l.confirm(d['id'],True)
        self.assertEqual(self.l.balances('2026-10-28')[self.a],94783)
        self.assertEqual(self.l.rows('SELECT balance FROM credit_cards WHERE id=?',(key,))[0]['balance'],100000)
        self.assertEqual(len(self.l.calendar('2026-10-18','2026-11-30')),1)

    def test_other_card_keeps_unknown_debt_and_allocation_not_sber_terms(self):
        sber=self.save_card()
        halva=self.l.card_save('Халва',None,None,'2026-10-18','user:halva-v1','Отдельная карта, условия долга не сообщены',due_day=23,first_due='2026-10-23',account=self.a,monthly_payment='95.24',interest_only=None)['id']
        row=self.l.rows('SELECT * FROM credit_cards WHERE id=?',(halva,))[0]
        self.assertIsNone(row['balance']);self.assertIsNone(row['interest_only']);self.assertIsNone(row['monthly_interest']);self.assertIsNone(row['annual_rate'])
        self.assertEqual(row['monthly_payment'],9524)
        self.assertEqual(self.l.card_scenario(halva)['status'],'insufficient_terms')
        self.assertIsNone(self.l.card_scenario(halva)['payoff_months'])
        october=self.l.calendar('2026-10-18','2026-10-31')
        self.assertEqual([(e['date'],e['amount']) for e in october],[('2026-10-23',9524),('2026-10-28',5217)])
        self.assertEqual(self.l.forecast('2026-10-18','2026-10-31')['days'][-1]['total'],85259)
        self.assertEqual(self.l.rows('SELECT balance FROM credit_cards WHERE id=?',(sber,))[0]['balance'],100000)

    def test_card_same_month_planning_no_double_oneoff_and_reminder_recovery(self):
        self.l.schedule('Халва','expense','60.00',self.a,date='2026-10-23')
        self.l.schedule('Халва','expense','60.00',self.a,monthday=23,start='2026-10-19')
        self.l.card_save('Халва',None,None,'2026-10-18','user:halva-v1','Без выдуманного долга',due_day=23,first_due='2026-10-23',account=self.a,monthly_payment='95.24',interest_only=None)
        self.assertEqual(len(self.l.calendar('2026-10-18','2026-10-31')),1)
        self.l.queue_reminders('2026-10-18','2026-10-31')
        reminder=self.l.rows('SELECT * FROM reminders')[0];self.assertEqual(reminder['due'],'2026-10-22')
        with self.l.transaction():self.l.db.execute("UPDATE reminders SET state='cancelled' WHERE id=?",(reminder['id'],))
        self.l.queue_reminders('2026-10-18','2026-10-31')
        self.assertEqual(self.l.rows('SELECT state FROM reminders')[0]['state'],'pending')
        with self.l.transaction():self.l.db.execute("UPDATE reminders SET state='sent' WHERE id=?",(reminder['id'],))
        self.l.queue_reminders('2026-10-18','2026-10-31')
        self.assertEqual(self.l.rows('SELECT state FROM reminders')[0]['state'],'sent')


if __name__=='__main__':unittest.main()
