import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from local_finance.assistant import Ledger, Assistant
from local_finance.changes import apply_change


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');Assistant(self.l,None)
        self.a=self.l.family_opening('0','2026-10-19','opening')['account']
        self.post('expense','4005','2026-10-20','old')
        self.post('income','117000','2026-10-21','salary')
        for n,amount in enumerate(('490','229','224')):self.post('expense',amount,'2026-10-21',str(n))
        self.clock=patch('finance.reconciliation.local_today',return_value='2026-10-23');self.clock.start()

    def tearDown(self):self.clock.stop();self.l.db.close()

    def post(self,kind,amount,date,source):
        d=self.l.stage({'kind':kind,'amount':amount,'date':date,'account':self.a,'ambiguities':[]},source)
        return self.l.confirm(d['id'],True)

    def fix(self,**kw):
        return self.l.balance_reconcile(**{'amount':'116057','as_of':'2026-10-21','source':'correction','reason':'Сообщён владельцем',**kw})

    def test_real_case_preserves_every_posting_and_account_and_history(self):
        ops=self.l.rows('SELECT * FROM operations');accounts=self.l.rows('SELECT * FROM accounts')
        self.assertEqual(self.l.balances('2026-10-21')[self.a],11205200)
        r=self.fix(expected_balance='112052');self.assertEqual(r['delta'],400500)
        self.assertFalse(r['cash_posting_created'])
        self.assertEqual(self.l.balances('2026-10-21')[self.a],11605700)
        self.assertEqual(self.l.family_forecast('2026-10-21','2026-10-22')['actual_balance'],11605700)
        self.assertEqual(self.l.balances('2026-10-20')[self.a],-400500)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),ops)
        self.assertEqual(self.l.rows('SELECT * FROM accounts'),accounts)

    def test_retry_after_new_purchase_never_resets_balance(self):
        self.fix();self.post('expense','100','2026-10-21','later')
        self.assertTrue(self.fix()['duplicate'])
        self.assertEqual(self.l.balances('2026-10-21')[self.a],11595700)
        self.post('income','500','2026-10-22','next')
        self.assertEqual(self.l.balances('2026-10-22')[self.a],11645700)
        self.assertEqual(len(self.l.snapshot()['balance_adjustments']),1)

    def test_second_correction_and_zero_and_negative_are_absolute(self):
        self.fix();self.fix(amount='0',source='second')
        self.assertEqual(self.l.balances('2026-10-21')[self.a],0)
        self.fix(amount='-50.15',source='third',as_of='2026-10-22')
        self.assertEqual(self.l.balances('2026-10-22')[self.a],-5015)

    def test_invalid_changes_rollback(self):
        for kw in ({'as_of':'2026-10-18'},{'as_of':'2026-10-24'},{'amount':1.1},{'reason':''},{'source':''},{'account':'missing'},{'expected_balance':'0'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.fix(**kw)
        self.assertEqual(self.l.rows('SELECT * FROM balance_adjustments'),[])
        with self.assertRaises(RuntimeError),self.l.transaction():
            self.fix();raise RuntimeError('outer transaction failed')
        self.assertEqual(self.l.rows('SELECT * FROM balance_adjustments'),[])

    def test_source_conflict_and_older_correction_rejected(self):
        self.fix()
        for kw in ({'amount':'5'},{'as_of':'2026-10-22'},{'reason':'other'},{'account':'other'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.fix(**kw)
        with self.assertRaises(ValueError):self.fix(source='backdated',as_of='2026-10-20')

    def test_multiple_accounts_need_explicit_account_without_allocation(self):
        b=self.l.account('Другой','100','2026-10-19')['id']
        with self.assertRaises(ValueError):self.fix()
        self.fix(account=self.a)
        self.assertEqual(self.l.balances('2026-10-21')[b],10000)

    def test_immediate_dispatch_requires_quote_and_retry_is_durable(self):
        payload={'amount':'116057','as_of':'2026-10-21','reason':'Уточнение владельца','expected_balance':'112052'}
        body='Установи баланс 116 057 рублей, не добавляя повторно зарплату и расходы'
        with self.assertRaises(PermissionError):apply_change(self.l,412850740,'owner:1','balance_reconcile',payload,body,'нет такой цитаты')
        r=apply_change(self.l,412850740,'owner:1','balance_reconcile',payload,body,body)
        self.assertTrue(r['applied']);self.assertEqual(r['result']['reported'],11605700)
        self.assertEqual(self.l.rows('SELECT * FROM local_pending'),[])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'copy.sqlite3';c=Ledger(path);self.l.db.backup(c.db);c.db.close()
            c=Ledger(path)
            try:
                self.assertTrue(apply_change(c,412850740,'owner:1','balance_reconcile',payload,body,body)['duplicate'])
                self.assertEqual(c.balances('2026-10-21')[self.a],11605700)
            finally:c.db.close()


if __name__=='__main__':unittest.main()
