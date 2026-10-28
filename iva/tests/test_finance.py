import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT/'plugin'/'family-finance'
sys.path.insert(0,str(PLUGIN))
from finance.core import Ledger,money,month_day
from finance.delivery import deliver


class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.l = Ledger(':memory:')
        self.a = self.l.account('Карта','1000.10','2026-10-01')['id']
        self.b = self.l.account('Наличные','500.00','2026-10-01')['id']

    def tearDown(self): self.l.db.close()

    def post(self,kind,amount,when='2026-10-02',source='tg:1',**kwargs):
        draft = self.l.stage(dict(kind=kind,amount=amount,date=when,account=self.a,**kwargs),source)
        return self.l.confirm(draft['id'],True)

    def test_exact_money_and_rejections(self):
        self.assertEqual(money('1 234,56'),123456)
        self.assertEqual(money('0.10')+money('0.20'),30)
        for invalid in [0.1,'NaN','Infinity','1.001','1e999999','abc']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): money(invalid)

    def test_transfer_is_atomic_no_double_count(self):
        self.post('transfer','100.10',target=self.b)
        balances = self.l.balances('2026-10-02')
        self.assertEqual(balances,{self.a:90000,self.b:60010})
        self.assertEqual(sum(balances.values()),150010)
        self.assertEqual(len(self.l.snapshot()['operations']),1)

    def test_confirmation_ambiguity_and_correction(self):
        d = self.l.stage({'kind':'expense','amount':'20.00','date':'2026-10-02','account':self.a,'ambiguities':['Итог 20 или 200?']},'receipt:1')
        self.assertEqual(self.l.balances('2026-10-02')[self.a],100010)
        with self.assertRaises(ValueError): self.l.confirm(d['id'],True)
        op = self.l.confirm(d['id'],True,{'amount':'200.00','ambiguities':[]})
        self.assertEqual(self.l.balances('2026-10-02')[self.a],80010)
        self.l.void(op['id'],'Ошибочная сумма')
        self.post('expense','20.00',source='receipt:1:correction')
        self.assertEqual(self.l.balances('2026-10-02')[self.a],98010)
        self.assertTrue(self.l.rows("SELECT * FROM audit WHERE action='void'"))

    def test_duplicate_source_and_similarity(self):
        op = self.post('expense','20.00')
        repeated = self.post('expense','20.00')
        self.assertEqual(op['id'],repeated['id'])
        d = self.l.stage({'kind':'expense','amount':'20.00','date':'2026-10-02','account':self.a},'tg:2')
        self.assertTrue(self.l.confirm(d['id'],True)['requires_user_confirmation'])
        self.l.confirm(d['id'],True,allow_similar=True)
        self.assertEqual(len(self.l.snapshot()['operations']),2)

    def test_expected_salary_reconciled_and_future_facts(self):
        self.l.schedule('Зарплата','income','100.00',self.a,date='2026-10-05')
        e = self.l.calendar('2026-10-01','2026-10-31')[0]
        self.post('income','110.00','2026-10-05',occurrence=e['occurrence'])
        f = self.l.forecast('2026-10-01','2026-10-06')
        self.assertEqual(f['days'][-1]['total'],161010)
        self.assertEqual(len(self.l.calendar('2026-10-01','2026-10-31')),0)
        with self.assertRaises(Exception):
            self.post('income','100.00','2026-10-06',source='other',occurrence=e['occurrence'])

    def test_late_payment_closes_only_original_occurrence(self):
        self.l.schedule('Зарплата','income','100.00',self.a,monthday=5,start='2026-10-01')
        e = self.l.calendar('2026-10-01','2026-10-31')[0]
        self.post('income','100.00','2026-10-06',occurrence=e['occurrence'])
        events = self.l.calendar('2026-10-01','2026-11-30')
        self.assertEqual([x['date'] for x in events],['2026-11-05'])

    def test_budget_must_be_explicitly_configured(self):
        self.assertFalse(self.l.forecast('2026-10-01','2026-10-02')['affordable'])
        self.l.configure('0.00','0.00')
        self.assertTrue(self.l.forecast('2026-10-01','2026-10-02')['affordable'])

    def test_gap_before_salary_despite_positive_month_end(self):
        self.l.schedule('Аренда','expense','1700.00',self.a,date='2026-10-03')
        self.l.schedule('Зарплата','income','5000.00',self.a,date='2026-10-10')
        f = self.l.forecast('2026-10-01','2026-10-31')
        self.assertEqual(f['first_gap'],'2026-10-03')
        self.assertGreater(f['days'][-1]['total'],0)
        self.assertFalse(f['affordable'])

    def test_same_day_gap_cannot_be_hidden_by_salary(self):
        self.l.schedule('Платёж','expense','1700.00',self.a,date='2026-10-03')
        self.l.schedule('Зарплата','income','5000.00',self.a,date='2026-10-03')
        f = self.l.forecast('2026-10-01','2026-10-03')
        self.assertEqual(f['first_gap'],'2026-10-03')
        self.assertGreater(f['days'][-1]['total'],0)

    def test_daily_budget_is_debited_before_same_day_income(self):
        self.l.configure('0.00','1100.00',self.a)
        self.l.schedule('Зарплата','income','5000.00',self.a,date='2026-10-02')
        f = self.l.forecast('2026-10-01','2026-10-02')
        self.assertEqual(f['first_gap'],'2026-10-02')
        self.assertGreater(f['days'][-1]['balances'][self.a],0)

    def test_future_transfer_preserves_forecast_total(self):
        self.l.configure('0.00','0.00')
        self.post('transfer','400.00','2026-10-05',target=self.b)
        f = self.l.forecast('2026-10-01','2026-10-06')
        self.assertEqual(f['minimum'],150010)
        self.assertEqual(f['days'][-1]['total'],150010)
        self.assertTrue(f['affordable'])

    def test_purchase_daily_reserve_and_account_liquidity(self):
        self.l.configure('1000.00','10.00',self.a)
        self.assertTrue(self.l.forecast('2026-10-01','2026-10-10')['affordable'])
        f = self.l.forecast('2026-10-01','2026-10-10',{'amount':'450.00','account':self.a,'date':'2026-10-02'})
        self.assertFalse(f['affordable'])
        self.assertEqual(f['days'][-1]['total'],96010)
        self.l.configure('0.00','0.00')
        f = self.l.forecast('2026-10-01','2026-10-02',{'amount':'600.00','account':self.b,'date':'2026-10-02'})
        self.assertGreater(f['minimum'],0)
        self.assertFalse(f['affordable'])

    def test_overdue_expense_carried_income_not_assumed(self):
        self.l.schedule('Просрочено','expense','200.00',self.a,date='2026-10-02')
        self.l.schedule('Не пришло','income','300.00',self.a,date='2026-10-02')
        f = self.l.forecast('2026-10-03','2026-10-04')
        self.assertEqual(f['days'][-1]['total'],130010)
        self.assertEqual(len(f['overdue_unsettled']),2)

    def test_month_end_and_leap_year(self):
        self.assertEqual(str(month_day(2028,2,31)),'2028-02-29')
        self.l.schedule('31 числа','expense','1.00',self.a,monthday=31,start='2026-10-01')
        events = self.l.calendar('2026-10-01','2026-12-31')
        self.assertEqual([e['date'] for e in events],['2026-10-31','2026-11-30','2026-12-31'])

    def test_reminder_lead_idempotence_and_failure(self):
        self.l.schedule('Аренда','expense','100.00',self.a,date='2026-10-10',lead_days=3)
        self.assertEqual(deliver(self.l,'2026-10-06','2026-11-01'),[])
        sent = []
        sender = lambda text:sent.append(text) or 42
        self.assertEqual(deliver(self.l,'2026-10-07','2026-11-01',sender)[0]['state'],'sent')
        deliver(self.l,'2026-10-08','2026-11-01',sender)
        self.assertEqual(len(sent),1)
        self.l.schedule('Связь','expense','20.00',self.a,date='2026-10-11',lead_days=3)
        def failure(text): raise TimeoutError()
        self.assertEqual(deliver(self.l,'2026-10-08','2026-11-01',failure)[0]['state'],'uncertain')
        self.assertEqual(deliver(self.l,'2026-10-09','2026-11-01',sender),[])

    def test_cancel_and_paid_reminder(self):
        plan = self.l.schedule('Аренда','expense','100.00',self.a,date='2026-10-10')
        deliver(self.l,'2026-10-06','2026-11-01')
        self.l.cancel_schedule(plan['id'],'2026-10-01')
        self.assertEqual(deliver(self.l,'2026-10-07','2026-11-01'),[])

    def test_reminder_after_downtime_is_not_lost(self):
        self.l.schedule('Платёж','expense','100.00',self.a,date='2026-10-05',lead_days=2)
        self.assertEqual(len(deliver(self.l,'2026-10-07','2026-11-01')),1)

    def test_default_reminder_is_one_calendar_day_before_and_once(self):
        self.l.schedule('Обязательный платёж','expense','100.00',self.a,date='2026-10-20')
        self.assertEqual(deliver(self.l,'2026-10-18','2026-11-01'),[])
        sent = []
        sender = lambda text:sent.append(text) or 42
        result = deliver(self.l,'2026-10-19','2026-11-01',sender)
        self.assertEqual(result[0]['state'],'sent')
        self.assertEqual(self.l.rows('SELECT due FROM reminders')[0]['due'],'2026-10-19')
        self.assertEqual(deliver(self.l,'2026-10-19','2026-11-01',sender),[])
        self.assertEqual(len(sent),1)

    def test_loan_known_zero_rate_and_interest(self):
        loan = self.l.loan('Без %','1200.00','0','100.00','2026-10-31',31,12)['id']
        f = self.l.loan_scenario(loan)
        self.assertEqual((f['months'],f['overpayment'],f['total_paid']),(12,0,120000))
        self.assertEqual(f['schedule'][4]['date'],'2027-02-28')
        interest = self.l.loan('12%','1000.00','12','510.00','2026-10-20',20,2)['id']
        f = self.l.loan_scenario(interest)
        self.assertEqual(f['months'],2)
        self.assertEqual(f['overpayment'],1500)
        self.assertEqual(f['total_paid'],101500)

    def test_six_loans_compare_scenarios_and_conservation(self):
        for i in range(6):
            key = self.l.loan(str(i),'100000.00',str(12+i),'4000.00','2026-10-28',28,36)['id']
            base = self.l.loan_scenario(key)
            extra = self.l.loan_scenario(key,extra='1000.00',lump_sum='5000.00')
            recast = self.l.loan_scenario(key,lump_sum='10000.00',mode='payment')
            self.assertLess(extra['months'],base['months'])
            self.assertLess(extra['overpayment'],base['overpayment'])
            self.assertLess(recast['payment'],400000)
            self.assertEqual(sum(r['principal'] for r in extra['schedule'])+extra['lump_sum'],10000000)
            self.assertEqual(sum(r['payment'] for r in extra['schedule'])+extra['lump_sum'],extra['total_paid'])
        self.assertEqual(len(self.l.snapshot()['loans']),6)

    def test_recast_rounding_does_not_add_spurious_month(self):
        key = self.l.loan('Рассрочка','100.00','0','40.00','2026-10-20',20,3)['id']
        f = self.l.loan_scenario(key,mode='payment')
        self.assertEqual(f['months'],3)
        self.assertEqual(f['schedule'][-1]['payment'],3334)

    def test_negative_amortization_and_invalid_inputs(self):
        key = self.l.loan('Невозможный','100000.00','40','100.00','2026-10-20',20,12)['id']
        with self.assertRaises(ValueError): self.l.loan_scenario(key)
        with self.assertRaises(ValueError): self.post('transfer','1.00',target=self.a)
        with self.assertRaises(ValueError): self.post('expense','-1.00')
        with self.assertRaises(ValueError): self.post('expense','1.00','2026-10-01')

    def test_backup_restore(self):
        self.post('expense','10.00')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'backup.sqlite3'
            self.l.backup(path)
            restored = Ledger(path)
            self.assertEqual(restored.balances('2026-10-02'),self.l.balances('2026-10-02'))
            restored.db.close()

    def test_mcp_real_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            requests = [
                {'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                {'jsonrpc':'2.0','method':'notifications/initialized'},
                {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'finance','arguments':{'action':'account','payload':{'name':'Русский счёт','opening':'0.00','as_of':'2026-10-01'}}}},
                {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'finance','arguments':{'action':'snapshot','payload':{}}}}
            ]
            process = subprocess.run([sys.executable,str(PLUGIN/'server.py'),'--db',str(Path(tmp)/'ledger.db')],input='\n'.join(json.dumps(r) for r in requests)+'\n',text=True,encoding='utf-8',capture_output=True,check=True)
            replies = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual([r['id'] for r in replies],[1,2,3,4])
            self.assertEqual(replies[1]['result']['tools'][0]['name'],'finance')
            snap = json.loads(replies[-1]['result']['content'][0]['text'])
            self.assertEqual(len(snap['accounts']),1)
            self.assertEqual(snap['accounts'][0]['name'],'Русский счёт')


if __name__=='__main__': unittest.main()
