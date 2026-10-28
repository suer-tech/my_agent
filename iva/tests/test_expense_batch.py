from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_finance.assistant import Ledger, Assistant
from local_finance.bot import OWNER
from local_finance.received_income import ZONE
from local_finance.expense_batch import record
from local_finance.iva_telegram import IvaAssistant,EveBridge
from iva_integration.gateway import actual_cash

TEXT='Расходы внеси эти: 1500 это на подарок, 858 продукты, 1584 детское питание, еще 864 продукты'
class NoModel:
    def send(self,*args):raise AssertionError('Explicit list must not invoke model')

class ExpenseBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'finance.sqlite3'
        self.l=Ledger(self.path);Assistant(self.l,None);self.uid=0
        self.account=self.l.family_opening('8000','2026-10-19','fixture:opening')['account']
        for ref,name in [('original-monthly-line-15','Продукты'),('original-monthly-line-16','Детское питание')]:
            self.l.family_expense_save(ref,name,name,'10000','2026-10','fixture:'+ref)
    def tearDown(self):self.l.db.close();self.tmp.cleanup()
    def incoming(self,text=TEXT,**extra):
        self.uid+=1
        msg={'from':{'id':OWNER},'chat':{'id':OWNER,'type':'private'},'date':int(datetime(2026,10,19,18,34,tzinfo=ZONE).timestamp()),'text':text,**extra}
        with self.l.db:
            self.l.db.execute('INSERT INTO local_telegram_messages VALUES(?,?,?)',(self.uid,self.uid,json.dumps(msg)))
            self.l.db.execute("INSERT INTO local_updates(id,chat,text,state,metadata) VALUES(?,?,?,'processing','{}')",(self.uid,OWNER,text))
        return f'telegram:{OWNER}:{self.uid}:0'
    def test_owner_list_commits_four_separate_rows_without_model_and_correct_categories(self):
        s=self.incoming();answer=record(self.l,OWNER,s,TEXT)['text']
        rows=self.l.rows('SELECT amount,note FROM operations ORDER BY rowid')
        self.assertEqual([r['amount'] for r in rows],[150000,85800,158400,86400])
        self.assertIn('4806.00',answer);self.assertEqual(self.l.balances('2026-10-19')[self.account],319400)
        self.assertEqual(len(self.l.rows("SELECT * FROM family_expense_receipts WHERE ref='original-monthly-line-15'")),2)
        self.assertEqual(len(self.l.rows("SELECT * FROM family_expense_receipts WHERE ref='original-monthly-line-16'")),1)
        cash=actual_cash(self.l)
        self.assertEqual(cash['actual_balance'],319400)
        self.assertTrue(cash['account_opening_is_not_current_balance'])
    def test_same_source_retry_and_database_reopen_no_double_debit(self):
        s=self.incoming();record(self.l,OWNER,s,TEXT)
        self.l.db.close();self.l=Ledger(self.path)
        self.assertTrue(record(self.l,OWNER,s,TEXT)['duplicate']);self.assertEqual(len(self.l.rows('SELECT * FROM operations')),4)
    def test_resending_same_list_new_message_is_not_another_four_purchases(self):
        s=self.incoming();record(self.l,OWNER,s,TEXT)
        s=self.incoming();self.assertTrue(record(self.l,OWNER,s,TEXT)['duplicate'])
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),4)
    def test_failure_on_second_post_rolls_back_entire_list_and_retry_succeeds(self):
        s=self.incoming();original=self.l.confirm;calls=0
        def fail(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('Injected failure after first expense')
            return original(*args,**kwargs)
        with patch.object(self.l,'confirm',side_effect=fail):
            with self.assertRaises(RuntimeError):record(self.l,OWNER,s,TEXT)
        self.assertFalse(self.l.rows('SELECT * FROM operations'));self.assertFalse(self.l.rows('SELECT * FROM drafts'))
        self.assertTrue(record(self.l,OWNER,s,TEXT)['applied'])
    def test_malformed_list_and_conflicting_total_do_not_partially_post(self):
        for text in ['Внеси расходы: 100 продукты, непонятная строка','Внеси расходы: 100 продукты, итого 200']:
            s=self.incoming(text);self.assertIn('question',record(self.l,OWNER,s,text))
        self.assertFalse(self.l.rows('SELECT * FROM operations'))
    def test_plans_examples_and_forwarded_lists_never_auto_post(self):
        for text in ['Например '+TEXT,'Планирую '+TEXT,'Не '+TEXT,TEXT+' если получу зарплату']:
            s=self.incoming(text);self.assertIsNone(record(self.l,OWNER,s,text))
        s=self.incoming(TEXT,forward_origin={'type':'user'});self.assertIsNone(record(self.l,OWNER,s,TEXT))
        self.assertFalse(self.l.rows('SELECT * FROM operations'))
    def test_decimal_amounts_and_total_not_extra_expense(self):
        text='Внеси расходы: 100,50 продукты; 20.25 подарок; итого 120.75'
        s=self.incoming(text);result=record(self.l,OWNER,s,text)
        self.assertEqual(result['total'],'120.75');self.assertEqual(len(result['items']),2)
    def test_diagnostics_cannot_contaminate_live_session(self):
        with self.assertRaises(ValueError):EveBridge(skip_daily=True)
        EveBridge(state_dir=Path(self.tmp.name)/'diagnostic',skip_daily=True)

if __name__=='__main__':unittest.main()
