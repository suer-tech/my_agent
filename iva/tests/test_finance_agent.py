import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_finance.assistant import Ledger, Assistant
from local_finance.codex_cli import Codex
from iva_integration.finance_agent import record_actual, run, setup,save_plan


class FinanceAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'finance.sqlite3'
        self.l=Ledger(self.path);Assistant(self.l,None);setup(self.l)
        self.account=self.l.family_opening('10000','2026-10-19','fixture:opening')['account']
        self.body='Потратил 1500 на подарок и 858 на продукты'
        self.ops=[{'kind':'expense','amount':amount,'date':'2026-10-19','evidence':self.body,'note':note} for amount,note in [('1500','Подарок'),('858','Продукты')]]

    def tearDown(self):
        self.l.db.close();self.tmp.cleanup()

    def test_specialist_saves_credit_terms_without_proposal_and_caches_result(self):
        body='Сохрани по Сберу долг 181594.90, платёж 5217.17, ставка 36 процентов, осталось 59 платежей'
        payload={'name':'Сбер','balance':'181594.90','monthly_interest':None,'monthly_payment':'5217.17','interest_only':None,'reported_as_of':'2026-10-19','notes':'Ставка 36 процентов; осталось 59 платежей по сообщению владельца'}
        class Stub:
            model='gpt-6.1-sol';reasoning_effort='medium';last_usage={'input_tokens':1,'output_tokens':1}
            def __init__(s):s.count=0
            def structured(s,*args,**kwargs):
                s.count+=1
                if s.count==1:return {'text':'','calls':[{'name':'finance_apply','arguments_json':json.dumps({'action':'card_save','payload_json':json.dumps(payload),'evidence':body})}]}
                return {'text':'Данные по карте Сбера сохранены.','calls':[]}
        cli=Stub();result=run(body,'fixture:card',self.path,cli,body)
        self.assertTrue(result['steps'][0]['ok'])
        self.assertTrue(run(body,'fixture:card',self.path,cli,body)['cached'])
        self.assertEqual(cli.count,2)
        self.assertEqual(self.l.rows('SELECT * FROM local_pending'),[])
        self.assertEqual(self.l.rows('SELECT balance FROM credit_cards'),[{'balance':18159490}])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_atomic_batch_and_same_source_retry_after_reopen(self):
        record_actual(self.l,'fixture:1',self.ops,self.body)
        self.l.db.close();self.l=Ledger(self.path)
        self.assertTrue(record_actual(self.l,'fixture:1',self.ops,self.body)['duplicate'])
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),2)
        self.assertEqual(self.l.balances('2026-10-19')[self.account],764200)

    def test_second_write_failure_rolls_back_everything(self):
        original=self.l.confirm;count=0
        def fail(*args,**kwargs):
            nonlocal count
            count+=1
            if count==2:raise RuntimeError('injected disk failure')
            return original(*args,**kwargs)
        with patch.object(self.l,'confirm',side_effect=fail):
            with self.assertRaises(RuntimeError):record_actual(self.l,'fixture:1',self.ops,self.body)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])
        self.assertEqual(self.l.rows('SELECT * FROM local_agent_postings'),[])

    def test_unsubstantiated_amount_and_hypothesis_refused(self):
        wrong=[{**self.ops[0],'amount':'9999'}]
        with self.assertRaises(ValueError):record_actual(self.l,'fixture:bad',wrong,self.body)
        hypothetical='Если потратил 1500 на подарок'
        with self.assertRaises(ValueError):record_actual(self.l,'fixture:bad',[{**self.ops[0],'evidence':hypothetical}],hypothetical)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_separate_message_similar_purchase_requires_clarification(self):
        record_actual(self.l,'fixture:1',self.ops,self.body)
        with self.assertRaises(ValueError):record_actual(self.l,'fixture:2',self.ops,self.body)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),2)

    def test_duplicate_inside_one_model_batch_is_refused(self):
        with self.assertRaises(ValueError):record_actual(self.l,'fixture:duplicate',[self.ops[0],self.ops[0]],self.body)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_clear_monthly_plan_and_partial_payment_no_confirm_or_double_count(self):
        body='В ежемесячные расходы внеси 678 рублей на VPS, 300 уже внес сегодня'
        payload={'ref':'vps','name':'VPS','category':'Связь','amount':'678','start_period':'2026-10'}
        result=save_plan(self.l,'fixture:vps','family_expense_save',payload,body,body)
        self.assertTrue(result['applied']);self.assertFalse(result['cash_posting_created'])
        op={'kind':'expense','amount':'300','date':'2026-10-19','budget_ref':'vps','evidence':body}
        record_actual(self.l,'fixture:vps',[op],body)
        self.assertEqual(self.l.rows('SELECT * FROM local_pending'),[])
        self.assertTrue(save_plan(self.l,'fixture:vps','family_expense_save',payload,body,body)['duplicate'])
        self.assertEqual(len(self.l.rows('SELECT * FROM family_expenses')),1)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
        self.assertEqual(self.l.rows('SELECT ref FROM family_expense_receipts'),[{'ref':'vps'}])
        self.assertEqual(self.l.balances('2026-10-19')[self.account],970000)

    def test_link_existing_payment_of_same_request_without_debit(self):
        body='VPS 678 в месяц, 300 уже оплатил сегодня'
        result=record_actual(self.l,'fixture:vps',[{'kind':'expense','amount':'300','date':'2026-10-19','evidence':body}],body)
        payload={'ref':'vps','name':'VPS','category':'Связь','amount':'678','start_period':'2026-10'}
        ids=[result['operations'][0]['operation_id']]
        save_plan(self.l,'fixture:vps','family_expense_save',payload,body,body,ids)
        self.assertTrue(save_plan(self.l,'fixture:vps','family_expense_save',payload,body,body,ids)['duplicate'])
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),1)
        self.assertEqual(len(self.l.rows('SELECT * FROM family_expense_receipts')),1)

    def test_plan_rejects_unverified_amount_and_foreign_payment_atomically(self):
        body='VPS 678 в месяц'
        payload={'ref':'vps','name':'VPS','category':'Связь','amount':'999','start_period':'2026-10'}
        with self.assertRaises(ValueError):save_plan(self.l,'fixture:vps','family_expense_save',payload,body,body)
        payload['amount']='678'
        r=record_actual(self.l,'fixture:other',[self.ops[0]],self.body)
        with self.assertRaises(PermissionError):save_plan(self.l,'fixture:vps','family_expense_save',payload,body,body,[r['operations'][0]['operation_id']])
        self.assertEqual(self.l.rows('SELECT * FROM family_expenses'),[])
        with self.assertRaises(PermissionError):save_plan(self.l,'fixture:vps','void',payload,body,body)

    def test_clear_actual_income_is_posted_without_pending_confirmation(self):
        body='Внеси доход: сегодня получил 8000 рублей за фриланс'
        class Stub:
            model='gpt-6.1-sol';reasoning_effort='medium';last_usage={}
            def __init__(s):s.count=0
            def structured(s,*args,**kwargs):
                s.count+=1
                if s.count==1:
                    operations=[{'kind':'income','amount':'8000','date':'2026-10-19','note':'Фриланс','evidence':body}]
                    return {'text':'','calls':[{'name':'record_actual','arguments_json':json.dumps({'operations_json':json.dumps(operations)})}]}
                return {'text':'Внесла доход 8000 рублей за фриланс.','calls':[]}
        result=run(body,'fixture:income',self.path,Stub(),body)
        self.assertTrue(result['steps'][0]['ok'])
        self.assertEqual(self.l.rows('SELECT kind,amount FROM operations'),[{'kind':'income','amount':800000}])
        self.assertEqual(self.l.rows('SELECT * FROM local_pending'),[])
        self.assertEqual(self.l.balances('2026-10-19')[self.account],1800000)

    def test_clear_expected_income_saved_as_plan_without_cash_or_confirmation(self):
        body='Ожидаю зарплату 120000 рублей ежемесячно 20 числа, внеси в план с октября'
        payload={'ref':'test_salary','name':'Зарплата','monthday':20,'start':'2026-10-20','amount':'120000'}
        before=self.l.balances('2026-10-19')
        result=save_plan(self.l,'fixture:expected','income_save',payload,body,body)
        self.assertTrue(result['applied']);self.assertFalse(result['cash_posting_created'])
        self.assertEqual(self.l.balances('2026-10-19'),before)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])
        self.assertEqual(self.l.rows('SELECT * FROM local_pending'),[])

    def test_specialist_loop_and_job_cache_do_not_execute_twice(self):
        class Stub:
            model='gpt-6.1-sol';reasoning_effort='medium'
            last_usage={'input_tokens':1,'output_tokens':1}
            def __init__(s):s.count=0
            def structured(s,*args,**kwargs):
                s.count+=1
                if s.count==1:return {'text':'','calls':[{'name':'record_actual','arguments_json':json.dumps({'operations_json':json.dumps(self.ops)})}]}
                return {'text':'Внесены расходы 2358 рублей.','calls':[]}
        cli=Stub()
        result=run('Внеси расходы','fixture:loop',self.path,cli,self.body)
        self.assertTrue(result['ok']);self.assertEqual(cli.count,2)
        self.assertTrue(run('Внеси расходы','fixture:loop',self.path,cli,self.body)['cached'])
        self.assertEqual(cli.count,2)
        self.assertEqual(len(self.l.rows('SELECT * FROM operations')),2)

    def test_uncertain_job_cannot_be_replayed(self):
        self.l.db.execute("INSERT INTO local_finance_agent_jobs VALUES('fixture:uncertain','processing',NULL,'[]')");self.l.db.commit()
        result=run('Внеси расходы','fixture:uncertain',self.path,body_override=self.body)
        self.assertTrue(result['needs_reconciliation'])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_roles_survive_cli_command_reload(self):
        for role,model,effort in [(None,'gpt-6-luna','low'),('finance','gpt-6.1-sol','medium')]:
            cli=Codex(role=role)
            command=cli.command(self.tmp.name,'schema.json','out.json')
            self.assertEqual(command[command.index('--model')+1],model)
            self.assertIn('model_reasoning_effort="'+effort+'"',command)

    def test_mixed_message_reaches_iva_before_any_financial_write(self):
        from local_finance.iva_telegram import IvaAssistant
        from local_finance.bot import OWNER
        class Bridge:
            def send(s,text,source):s.text=text;s.source=source;return 'Результат Ивы'
        bridge=Bridge()
        body=self.body+' и напомни завтра о звонке'
        result=IvaAssistant(self.l,bridge=bridge).handle(OWNER,body,'fixture:mixed')
        self.assertEqual(result,'Результат Ивы');self.assertIn(body,bridge.text)
        self.assertIn('finance_agent',bridge.text)
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_delegated_text_does_not_grant_owner_write_authority(self):
        class Stub:
            model='gpt-6.1-sol';reasoning_effort='medium';last_usage={}
            def __init__(s):s.count=0
            def structured(s,*args,**kwargs):
                s.count+=1
                if s.count==1:return {'text':'','calls':[{'name':'record_actual','arguments_json':json.dumps({'operations_json':json.dumps(self.ops)})}]}
                return {'text':'Нет исходного поручения владельца, запись не выполнена.','calls':[]}
        result=run(self.body,'diagnostic:unauthorized',self.path,Stub())
        self.assertFalse(result['steps'][0]['ok'])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])


if __name__=='__main__':unittest.main()
