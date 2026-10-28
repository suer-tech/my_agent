import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import Bot,OWNER
from local_finance.attachments import decode_text,validate_receipt
from finance import bank


class StubCLI:
    def plan(self,prompt):return 'Ответ','none',{}


class StubAPI:pass


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');self.a=self.l.account('Карта','20000.00','2026-10-18')['id'];self.assistant=Assistant(self.l,StubCLI())
    def tearDown(self):self.l.db.close()
    def document(self,key='a',**changes):
        rows=[{'date':'2026-10-20','payment':'5900.12','principal':'5000.00','interest':'900.12','fees':'0.00','balance':None,'balance_before':'10000.00','page':1,'row_ref':'p1:r1'},
              {'date':'2026-11-23','payment':'5100.00','principal':'5000.00','interest':'100.00','fees':'0.00','balance':None,'balance_before':'5000.00','page':2,'row_ref':'p2:r1'}]
        payload={'kind':'loan_schedule','currency':'RUB','rows':rows,'questions':[],'pages_covered':[1,2],**changes}
        with self.l.transaction():self.l.db.execute('INSERT INTO source_documents VALUES(?,?,?,?,?,?)',(key,'private-original.bin','loan_schedule',2,json.dumps(payload),'extracted'))
        return key

    def test_exact_schedule_replaces_approximate_without_actual_expense(self):
        self.l.schedule('Альфа','expense','5900.00',self.a,monthday=20,start='2026-10-19')
        result=self.l.bank_import(self.document(),'Альфа',self.a)
        f=self.l.forecast('2026-10-18','2026-11-30')
        self.assertEqual(f['days'][-1]['total'],899988)
        self.assertEqual([e['date'] for e in self.l.calendar('2026-10-19','2026-11-30')],['2026-10-20','2026-11-23'])
        self.assertEqual(self.l.snapshot()['operations'],[])
        self.assertEqual(self.l.balances('2026-11-30')[self.a],2000000)
        self.assertEqual(len(self.l.rows('SELECT * FROM bank_payments')),2)
        with self.assertRaises(ValueError):self.l.schedule('Альфа','expense','5900.00',self.a,monthday=20,start='2026-10-19')

    def test_bank_import_is_duplicate_proof_and_versions_preserve_old_source(self):
        first=self.l.bank_import(self.document(),'Альфа',self.a)
        again=self.l.bank_import('a','Альфа',self.a)
        self.assertTrue(again['duplicate']);self.assertEqual(first['id'],again['id'])
        newer=self.l.bank_import(self.document('b'),'Альфа',self.a)
        self.assertEqual(newer['version'],2)
        self.assertEqual(len(self.l.rows('SELECT * FROM bank_payments')),4)
        self.assertEqual(len(self.l.calendar('2026-10-19','2026-11-30')),2)
        self.assertEqual(len(self.l.rows('SELECT * FROM source_documents')),2)

    def test_unbound_schedule_preserves_exact_data_but_forecast_requires_account(self):
        result=self.l.bank_import(self.document(),'Альфа')
        with self.assertRaises(ValueError):self.l.forecast('2026-10-18','2026-11-30')
        self.assertEqual(len(self.l.calendar('2026-10-19','2026-11-30')),2)
        self.assertEqual(self.l.queue_reminders('2026-10-19','2026-11-30')[0]['due'],'2026-10-19')
        self.l.bank_bind(result['id'],self.a)
        self.assertEqual(self.l.forecast('2026-10-18','2026-11-30')['days'][-1]['total'],899988)

    def test_page_coverage_and_money_components_must_be_complete(self):
        self.document(pages_covered=[1])
        with self.assertRaises(ValueError):self.l.bank_import('a','Альфа',self.a)
        self.assertEqual(self.l.snapshot()['bank_schedules'],[])
        key=self.document('bad')
        data=json.loads(self.l.rows('SELECT extraction FROM source_documents WHERE id=?',(key,))[0]['extraction'])
        data['rows'][0]['principal']='5100.00'
        with self.l.transaction():self.l.db.execute('UPDATE source_documents SET extraction=? WHERE id=?',(json.dumps(data),key))
        with self.assertRaises(ValueError):self.l.bank_import(key,'Альфа',self.a)

    def test_zero_payment_rows_stored_but_not_reminded(self):
        zero={'date':'2026-10-19','payment':'0.00','principal':'0.00','interest':'0.00','fees':'0.00','balance':None,'page':1,'row_ref':'p1:zero'}
        self.document(rows=[zero])
        self.l.bank_import('a','Альфа',self.a)
        self.assertEqual(len(self.l.rows('SELECT * FROM bank_payments')),1)
        self.assertEqual(self.l.calendar('2026-10-19','2026-11-30'),[])
        self.assertEqual(self.l.queue_reminders('2026-10-19','2026-11-30'),[])

    def test_duplicate_pdf_rows_rejected(self):
        key=self.document()
        data=json.loads(self.l.rows('SELECT extraction FROM source_documents WHERE id=?',(key,))[0]['extraction'])
        data['rows'].append(data['rows'][0])
        with self.l.transaction():self.l.db.execute('UPDATE source_documents SET extraction=? WHERE id=?',(json.dumps(data),key))
        with self.assertRaises(ValueError):self.l.bank_import(key,'Альфа',self.a)

    def test_balance_before_is_separate_from_payment_and_remaining_balance(self):
        self.l.bank_import(self.document(),'Альфа',self.a)
        row=self.l.rows('SELECT * FROM bank_payments ORDER BY on_date')[0]
        self.assertEqual(row['amount'],590012);self.assertEqual(row['balance_before'],1000000);self.assertIsNone(row['balance'])

    def test_txt_encodings_and_binary_rejection(self):
        text='Планируемые траты: связь 100 рублей'
        for encoding in ('utf-8','utf-16','cp1251'):self.assertEqual(decode_text(text.encode(encoding)),text)
        with self.assertRaises(ValueError):decode_text(b'\x00\x00\x00\x01binary')

    def test_receipt_item_mismatch_requires_clarification(self):
        data={'questions':[],'receipt':{'date':'2026-10-18','currency':'RUB','total':'300.00','items':[{'line_total':'100.00'}]}}
        self.assertTrue(validate_receipt(data))
        data['receipt']['items'].append({'line_total':'200.00'})
        self.assertEqual(validate_receipt(data),[])

    def test_owner_documents_and_captions_preserved_before_offset_advances(self):
        bot=Bot(self.l,self.assistant,StubAPI())
        msg={'message_id':7,'chat':{'type':'private','id':OWNER},'from':{'id':OWNER},'caption':'Это график Альфа','document':{'file_id':'synthetic-file-id','file_unique_id':'unique','file_name':'bank.pdf','mime_type':'application/pdf','file_size':42}}
        bot.ingest([{'update_id':99,'message':msg}])
        row=self.l.rows('SELECT * FROM local_updates')[0]
        self.assertEqual(row['text'],'/incoming_pdf');self.assertEqual(json.loads(row['metadata'])['file_id'],'synthetic-file-id')
        raw=json.loads(self.l.rows('SELECT message_json FROM local_telegram_messages')[0]['message_json'])
        self.assertEqual(raw['document']['file_id'],'synthetic-file-id');self.assertEqual(raw['caption'],'Это график Альфа')
        self.assertEqual(self.l.rows("SELECT value FROM local_meta WHERE key='offset'")[0]['value'],'100')

    def test_user_amount_clarification_updates_draft_with_provenance_not_money(self):
        row={'payment_no':51,'date':'2026-11-24','payment':None,'principal':None,'interest':None,'fees':None,'balance':None,'page':1,'row_ref':'photo1:51'}
        key=self.document(rows=[row],questions=['Обрезаны копейки'],pages_covered=[1,2])
        result=bank.amend(self.l,key,[{'payment_no':51,'payment':'100.21'}],[],'telegram:synthetic:reply')
        self.assertEqual(result['rows'][0]['payment'],'100.21')
        self.assertEqual(len(self.l.rows('SELECT * FROM source_document_revisions')),1)
        self.assertEqual(self.l.snapshot()['bank_schedules'],[])
        self.assertEqual(self.l.snapshot()['operations'],[])
        self.assertIn('user:telegram:',result['rows'][0]['row_ref'])
        self.l.bank_import(key,'Уточнённый кредит',self.a)
        with self.assertRaises(ValueError):bank.amend(self.l,key,[{'payment_no':51,'payment':'200.00'}],[],'later')


if __name__=='__main__':unittest.main()
