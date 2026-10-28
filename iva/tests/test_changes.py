import json
import unittest
from unittest.mock import patch
from local_finance.assistant import Ledger,Assistant,WRITE
from local_finance.changes import apply_change


class ChangesTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');Assistant(self.l,None)
        self.body='Сохрани сведения о кредите и исправь внутренний учёт'

    def tearDown(self):self.l.db.close()

    def apply(self,action,payload,source='test:change'):
        return apply_change(self.l,412850740,source,action,payload,self.body,self.body)

    def test_card_terms_immediate_and_idempotent_after_restart(self):
        payload={'name':'Сбер','balance':'181594.90','monthly_interest':None,'monthly_payment':'5217.17','interest_only':None,'reported_as_of':'2026-10-19','source':'model-forged-source','notes':'36% годовых; 59 платежей; реструктуризация 176136.98, смысл требует сверки','due_day':28,'first_due':'2026-10-28'}
        first=self.apply('card_save',payload)
        self.assertTrue(first['applied']);self.assertTrue(self.apply('card_save',payload)['duplicate'])
        self.assertEqual(len(self.l.rows('SELECT * FROM credit_card_reports')),1)
        row=self.l.rows('SELECT * FROM credit_cards')[0]
        self.assertEqual(row['balance'],18159490);self.assertIn('59 платежей',row['notes'])
        self.assertTrue(row['source'].startswith('owner-change:'))
        self.assertEqual(self.l.rows('SELECT * FROM local_pending'),[])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])
        debt=self.l.debt_inventory('2026-10-20')['debts']
        self.assertIn('59 платежей',next(r for r in debt if r['name']=='Сбер')['notes'])

    def test_loan_edit_preserves_identity_and_does_not_create_another_debt(self):
        payload={'name':'Сбер кредит','principal':'100000','annual_rate':'20','payment':'23000','first_due':'2026-11-16','monthday':16,'remaining_months':5}
        first=self.apply('loan',payload)['result']['id']
        changed=self.apply('loan',{**payload,'annual_rate':'18'},'test:correction')['result']
        self.assertEqual(changed['id'],first)
        rows=self.l.rows('SELECT * FROM loans');self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(rows[0]['payload'])['annual_rate'],'18')

    def test_account_schedule_cancel_and_void_need_no_codes(self):
        account=self.apply('account',{'name':'Общий','opening':'10000','as_of':'2026-10-19'})['result']['id']
        schedule=self.apply('schedule',{'name':'Интернет','kind':'expense','amount':'1105','account':account,'monthday':11,'start':'2026-10-01'})['result']['id']
        self.apply('cancel_schedule',{'schedule_id':schedule,'from_date':'2026-11-01'})
        draft=self.l.stage({'kind':'expense','amount':'100','date':'2026-10-20','account':account,'ambiguities':[]},'fixture:actual');op=self.l.confirm(draft['id'],True)['id']
        self.apply('void',{'operation_id':op,'reason':'Ошибочная запись'})
        self.assertEqual(self.l.rows('SELECT void FROM operations WHERE id=?',(op,))[0]['void'],1)
        self.assertEqual(self.l.rows('SELECT * FROM local_pending'),[])

    def test_failed_write_is_atomic_and_cannot_become_confirm_proposal(self):
        with self.assertRaises(ValueError):self.apply('card_save',{'name':'Сбер','balance':'-1','monthly_interest':None,'monthly_payment':'5','reported_as_of':'2026-10-19','notes':'неверно'})
        self.assertEqual(self.l.rows('SELECT * FROM credit_cards'),[])
        self.assertEqual(self.l.rows('SELECT * FROM local_finance_changes'),[])
        with self.assertRaises(PermissionError):apply_change(self.l,412850740,'test:x','configure',{},'','')
        with self.assertRaises(ValueError):self.apply('stage',{'data':{}})

    def test_all_mutating_methods_have_immediate_dispatch_and_replay_guard(self):
        # Registry completeness: a newly exposed ledger edit must not silently
        # fall back to a proposal. Real validation is exercised above and by the
        # core contract suites; this checks dispatch for every registered action.
        for action in sorted(WRITE-{'stage','batch','loan'}):
            with self.subTest(action=action),patch.object(self.l,action,return_value={'id':'fixture'}) as method:
                payload={'loan_name':'fixture'} if action=='bank_import' else {}
                with patch('local_finance.intake.mark_bank'):
                    self.assertTrue(self.apply(action,payload)['applied'])
                    self.assertTrue(self.apply(action,payload)['duplicate'])
                self.assertEqual(method.call_count,1)


if __name__=='__main__':unittest.main()
