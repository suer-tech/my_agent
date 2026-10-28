import sys,json,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin/family-finance'))
from finance.core import Ledger
from iva_integration.web_browser import navigation_failed
class Repairs(unittest.TestCase):
 def test_restricted_transfer(self):
  l=Ledger(':memory:')
  a=l.account('cash','20000','2026-10-20')['id'];b=l.account('hold','0','2026-10-21')['id']
  op={'kind':'transfer','amount':'16000','date':'2026-10-21','account':a,'target':b,'note':'security'}
  with self.assertRaises(ValueError):l.validate_operation(op)
  l.account_policy(b,False,True)
  d=l.stage(op,'repair-test');one=l.confirm(d['id'],True);two=l.confirm(d['id'],True)
  self.assertTrue(two['duplicate']);self.assertEqual(one['id'],two['id'])
  self.assertEqual(sum(l.balances('2026-10-21').values()),2000000)
  self.assertEqual(sum(l.available_balances('2026-10-21').values()),400000)
  self.assertEqual(l.family_forecast('2026-10-21','2026-10-22')['actual_balance'],400000)
  l.account_policy(b,True,True);self.assertEqual(sum(l.available_balances('2026-10-21').values()),2000000)
  self.assertEqual(l.rows('SELECT count(*) n FROM operations')[0]['n'],1);l.db.close()
 def test_document_schedule(self):
  l=Ledger(':memory:')
  loan=l.debt_save(debt_ref='ozon-test',name='Ozon',monthly_payment='206',due_day=20,planning_from_period='2026-10',reported_as_of='2026-10-21',source='test',notes='test')['id']
  data={'questions':[],'currency':'RUB','pages_covered':[1],'rows':[{'date':'2026-10-20','payment':'206','page':1,'row_ref':'1'},{'date':'2026-11-20','payment':'177.87','page':1,'row_ref':'2'}]}
  l.db.execute('INSERT INTO source_documents VALUES(?,?,?,?,?,?)',('doc','test','loan_schedule',1,json.dumps(data),'reviewed'));l.db.commit()
  first=l.debt_schedule_from_document(loan,'doc','owner');again=l.debt_schedule_from_document(loan,'doc','owner')
  self.assertTrue(again['duplicate']);self.assertEqual(first['payments'],2)
  self.assertEqual(l.rows('SELECT amount FROM reported_loan_periods WHERE period=?',('2026-11',))[0]['amount'],17787)
  self.assertEqual(len(l.rows('SELECT * FROM reported_loans')),1);self.assertEqual(len(l.rows('SELECT * FROM operations')),0);l.db.close()
 def test_owner_quotes(self):
  from local_finance.received_income import raw_owner_input
  from iva_integration.personal_skills import execute
  from unittest.mock import patch
  l=Ledger(':memory:');l.db.executescript('CREATE TABLE local_updates(id INTEGER,chat INTEGER,state TEXT,metadata TEXT);CREATE TABLE local_telegram_messages(id INTEGER,message_json TEXT);')
  body='Мне нравится «Дюна»';msg={'text':body,'from':{'id':42},'chat':{'id':42,'type':'private'}}
  l.db.execute('INSERT INTO local_updates VALUES(1,42,?,?)',('processing','{}'));l.db.execute('INSERT INTO local_telegram_messages VALUES(1,?)',(json.dumps(msg),));l.db.commit()
  with patch('iva_integration.personal_skills.Store') as store:
   execute({'action':'remember'},l,42,'telegram:42:1:0')
   self.assertEqual(store.return_value.execute.call_args.args[2],body)
  msg['forward_origin']={'type':'user'};l.db.execute('UPDATE local_telegram_messages SET message_json=?',(json.dumps(msg),));l.db.commit()
  with self.assertRaises(PermissionError):execute({'action':'remember'},l,42,'telegram:42:1:0')
  l.db.close()
 def test_navigation(self):
  self.assertTrue(navigation_failed('chrome-error://chromewebdata/',200))
  self.assertTrue(navigation_failed('https://example.org',503))
  self.assertFalse(navigation_failed('https://example.org',200))
if __name__=='__main__':unittest.main()
