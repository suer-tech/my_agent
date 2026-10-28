import json
import unittest
from local_finance.assistant import Ledger,Assistant
from local_finance.bot import OWNER
from local_finance.document_updates import apply_existing_debt_document


class DocumentUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');Assistant(self.l,None)
        self.source=f'telegram:{OWNER}:900:0'
        self.payload=dict(debt_ref='tbank',name='Т-Банк',monthly_payment='6900',due_day=5,
                          planning_from_period='2026-10',reported_as_of='2026-10-19',notes='old',
                          reported_total_balance='148385.54',payment_is_estimate=True)
        self.l.debt_save(**self.payload,source='fixture')
        self.payload.update(reported_as_of='2026-10-20',reported_total_balance='148505.64',notes='условия справки')
        self.msg={'from':{'id':OWNER},'chat':{'id':OWNER,'type':'private'},'document':{'file_id':'fixture'},'caption':''}
        self.l.db.execute('INSERT INTO local_telegram_messages VALUES(900,900,?)',(json.dumps(self.msg),))
        self.l.db.execute("INSERT INTO local_updates(id,chat,text,metadata,state) VALUES(900,?,'/incoming_pdf',?,'processing')",(OWNER,json.dumps({'document_id':'doc'})))
        extraction={'bank':'Т-Банк','page_texts':[{'page':1,'text':'20.10.2026 Общая задолженность 148 505,64 рублей.'}]}
        self.l.db.execute("INSERT INTO source_documents VALUES('doc','fixture.pdf','other',1,?,'extracted')",(json.dumps(extraction),))
        self.l.db.commit()
    def tearDown(self):self.l.db.close()
    def apply(self):
        return apply_existing_debt_document(self.l,OWNER,self.source,'debt_save',self.payload)
    def test_captionless_statement_updates_once_and_preserves_plan(self):
        self.payload.update(monthly_payment='1',due_day=20)
        self.assertTrue(self.apply()['applied'])
        self.assertTrue(self.apply()['duplicate'])
        row=self.l.rows('SELECT * FROM reported_loans')[0]
        self.assertEqual((row['monthly_payment'],row['due_day'],row['reported_total_balance']),(690000,5,14850564))
        self.assertEqual(len(self.l.rows('SELECT * FROM reported_loans')),1)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])
    def test_foreign_or_forwarded_document_rejected(self):
        for update in [{'from':{'id':123}},{'forward_origin':{'type':'user'}}]:
            msg={**self.msg,**update}
            self.l.db.execute('UPDATE local_telegram_messages SET message_json=?',(json.dumps(msg),))
            with self.assertRaises(PermissionError):self.apply()
    def test_wrong_debt_or_unverified_amount_rejected(self):
        self.payload['debt_ref']='new'
        with self.assertRaises(ValueError):self.apply()
        self.payload['debt_ref']='tbank';self.payload['reported_total_balance']='999'
        with self.assertRaises(ValueError):self.apply()
    def test_historical_envelope_and_arbitrary_action_rejected(self):
        with self.assertRaises(ValueError):
            apply_existing_debt_document(self.l,OWNER,self.source,'void',{})
        self.l.db.execute("UPDATE local_updates SET state='done'")
        with self.assertRaises(PermissionError):self.apply()
    def test_ambiguous_bank_rejected(self):
        p={**self.payload,'debt_ref':'other','name':'Т-Банк второй'}
        self.l.debt_save(**p,source='fixture:two')
        with self.assertRaises(ValueError):self.apply()

    def test_specialist_accepts_real_captionless_envelope(self):
        import tempfile,sqlite3
        from pathlib import Path
        from iva_integration.finance_agent import run
        payload=self.payload
        class Stub:
            model='gpt-6.1-sol';reasoning_effort='medium'
            last_usage={'input_tokens':1,'output_tokens':1}
            def __init__(self):self.count=0
            def structured(self,*args,**kwargs):
                self.count+=1
                if self.count==1:
                    return {'text':'','calls':[{'name':'finance_apply','arguments_json':json.dumps(
                        {'action':'debt_save','payload_json':json.dumps(payload),'evidence':''})}]}
                return {'text':'Обновлено','calls':[]}
        self.l.db.commit()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.sqlite3'
            target=sqlite3.connect(path)
            try:self.l.db.backup(target)
            finally:target.close()
            result=run('Обнови данные по справке',self.source,path,Stub())
            self.assertTrue(result['steps'][0]['ok'])


if __name__=='__main__':unittest.main()
