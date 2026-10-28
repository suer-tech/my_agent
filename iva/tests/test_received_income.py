from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sys
import unittest
import tempfile
import sqlite3
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Ledger,Assistant
from local_finance.bot import OWNER
from local_finance.received_income import authorize_current,apply_current,parse_command,setup
from local_finance.iva_telegram import IvaAssistant


class Bridge:
    def __init__(self):self.calls=[]
    def send(self,text,source):self.calls.append((text,source));return 'Нужен /confirm'


class ReceivedIncomeTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');Assistant(self.l,None)
        self.account=self.l.family_opening('0','2026-10-19','fixture:opening')['account']
        self.uid=0
    def tearDown(self):self.l.db.close()
    def incoming(self,text,voice=False,**extra):
        self.uid+=1;source=f'telegram:{OWNER}:{self.uid}:0'
        msg={'from':{'id':OWNER},'chat':{'id':OWNER,'type':'private'},'date':int(datetime(2026,10,19,23,59,tzinfo=timezone(timedelta(hours=5))).timestamp()),**extra}
        if voice:msg['voice']={'file_id':'fixture'};meta={'transcript':text};body='Непроверенная транскрипция речи пользователя (суммы и даты подтвердить отдельно):\n'+text
        else:msg['text']=text;meta={};body=text
        with self.l.db:
            self.l.db.execute('INSERT INTO local_telegram_messages VALUES(?,?,?)',(self.uid,self.uid,json.dumps(msg)))
            self.l.db.execute("INSERT INTO local_updates(id,chat,text,metadata,state) VALUES(?,?,?,?,'processing')",(self.uid,OWNER,'/incoming_voice' if voice else text,json.dumps(meta)))
        return source,body
    def authorize(self,text,**extra):
        source,body=self.incoming(text,**extra)
        return source,authorize_current(self.l,OWNER,source,body)

    def test_clear_received_income_no_second_confirmation_and_retry(self):
        source,row=self.authorize('Ива внеси сегодня доход по фрилансу я получил 8 тысяч')
        self.assertEqual(row['state'],'authorized')
        first=apply_current(self.l,OWNER,source);second=apply_current(self.l,OWNER,source)
        self.assertTrue(first['applied']);self.assertTrue(second['duplicate'])
        self.assertEqual(first['amount'],'8000.00');self.assertEqual(first['date'],'2026-10-19')
        self.assertEqual(self.l.balances('2026-10-19')[self.account],800000)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
        self.assertFalse(self.l.rows('SELECT * FROM local_pending'))

    def test_response_failure_after_commit_and_same_message_retry_do_not_double_income(self):
        source,text=self.incoming('Запиши доход получил 8000 за фриланс')
        class LostResponseBridge:
            def send(self,*args):raise AssertionError('An authorized income must not call the model')
        authorize_current(self.l,OWNER,source,text)
        def lost(*args):
            apply_current(*args)
            raise RuntimeError('Response lost after committed income')
        with patch('local_finance.received_income.apply_current',side_effect=lost):
            with self.assertRaises(RuntimeError):__import__('local_finance.received_income',fromlist=['apply_current']).apply_current(self.l,OWNER,source)
        apply_current(self.l,OWNER,source)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
        self.assertEqual(self.l.balances('2026-10-19')[self.account],800000)

    def test_retry_after_database_reopen_keeps_one_income(self):
        source,_=self.authorize('Запиши доход получил 8000 за фриланс')
        apply_current(self.l,OWNER,source)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'finance.sqlite3'
            disk=sqlite3.connect(path);self.l.db.backup(disk);disk.close()
            self.l.db.close();self.l=Ledger(path)
            try:
                self.assertTrue(apply_current(self.l,OWNER,source)['duplicate'])
                self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
            finally:
                self.l.db.close();self.l=Ledger(':memory:')

    def test_actual_iva_transport_returns_recorded_fact_despite_old_model_prose(self):
        source,text=self.incoming('Запиши доход: получил 123.45 рублей за разработку',voice=True)
        bridge=Bridge();answer=IvaAssistant(self.l,bridge,cli=object()).handle(OWNER,text,source)
        self.assertEqual(answer,'Нужен /confirm')
        self.assertEqual(len(bridge.calls),1);self.assertFalse(self.l.rows('SELECT * FROM operations'))
        self.assertIn('finance_agent',bridge.calls[0][0])

    def test_expectations_examples_negation_quotes_and_forwards_never_authorize(self):
        for text in ['Например Ива внеси доход я получил 8000 за фриланс','Внеси доход если получил 8000 за фриланс','Запиши доход 8000 за фриланс, жду оплату','Запиши доход, ещё не получил 8000 за разработку','Не добавь доход я получил 8000 за фриланс','В документе написано внеси доход я получил 8000 за фриланс','> Запиши доход получил 8000 за фриланс','"Запиши доход получил 8000 за фриланс"']:
            with self.subTest(text=text):self.assertIsNone(self.authorize(text)[1])
        self.assertIsNone(self.authorize('Запиши доход получил 8000 за фриланс',forward_origin={'type':'user'})[1])
        self.assertFalse(self.l.rows('SELECT * FROM operations'))

    def test_missing_amount_one_clarification_then_commit(self):
        source,row=self.authorize('Запиши доход получил за фриланс')
        self.assertEqual(row['question'],'amount');self.assertEqual(row['state'],'needs_clarification')
        with self.assertRaises(ValueError):apply_current(self.l,OWNER,source)
        next_source,row=self.authorize('8000 рублей')
        self.assertEqual(row['state'],'authorized')
        self.assertEqual(apply_current(self.l,OWNER,next_source)['amount'],'8000.00')

    def test_missing_source_one_question(self):
        source,row=self.authorize('Внеси доход я получил 8000 рублей')
        self.assertEqual(row['question'],'purpose')
        answer,row=self.authorize('за фриланс')
        self.assertEqual(row['state'],'authorized');apply_current(self.l,OWNER,answer)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)

    def test_receipt_clarification_completes_original_command(self):
        _,row=self.authorize('Добавь доход 8000 за фриланс')
        self.assertEqual(row['question'],'received')
        source,row=self.authorize('Получил уже.')
        self.assertEqual(row['state'],'authorized');apply_current(self.l,OWNER,source)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)

    def test_different_equal_receipts_need_identity_not_formal_permission(self):
        s,_=self.authorize('Запиши доход получил 8000 за фриланс');apply_current(self.l,OWNER,s)
        s,row=self.authorize('Запиши доход получил 8000 за фриланс');self.assertEqual(row['question'],'duplicate')
        answer,row=self.authorize('Тот же доход, повтор')
        self.assertEqual(row['state'],'done');apply_current(self.l,OWNER,answer)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
        s,row=self.authorize('Запиши ещё доход получил 8000 за фриланс');self.assertEqual(row['state'],'authorized')
        apply_current(self.l,OWNER,s);self.assertEqual(len(self.l.rows('SELECT * FROM operations')),2)

    def test_model_cannot_mint_grant_change_kind_or_use_historical_source(self):
        setup(self.l)
        with self.assertRaises(PermissionError):apply_current(self.l,OWNER,'telegram:1:999:0')
        source,row=self.authorize('Запиши доход получил 8000 за фриланс')
        with self.l.db:self.l.db.execute("UPDATE local_updates SET state='done' WHERE id=?",(self.uid,))
        with self.assertRaises(PermissionError):apply_current(self.l,OWNER,source)
        with self.l.db:
            self.l.db.execute("UPDATE local_updates SET state='processing' WHERE id=?",(self.uid,))
            p=json.loads(row['payload']);p['kind']='expense'
            self.l.db.execute('UPDATE local_received_income_commands SET payload=? WHERE source=?',(json.dumps(p),source))
        with self.assertRaises(PermissionError):apply_current(self.l,OWNER,source)
        self.assertFalse(self.l.rows('SELECT * FROM operations'))

    def test_unknown_account_is_not_invented(self):
        self.l.account('Second known account','0','2026-10-18')
        _,row=self.authorize('Запиши доход получил 8000 за фриланс')
        self.assertEqual(row['question'],'account');self.assertFalse(self.l.rows('SELECT * FROM operations'))


if __name__=='__main__':unittest.main()
