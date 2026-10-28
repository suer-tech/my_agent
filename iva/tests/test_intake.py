import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Assistant,Ledger
from local_finance import intake
from local_finance.bot import OWNER


class FakeCLI:
    def __init__(self,*answers):self.answers=list(answers);self.prompts=[]
    def plan(self,prompt):self.prompts.append(prompt);return self.answers.pop(0)


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');self.a=self.l.account('Карта','1000.00','2026-10-01')['id']
        self.assistant=Assistant(self.l,FakeCLI())
    def tearDown(self):self.l.db.close()
    def schedule(self,name='Платёж',amount='100.00',monthday=20,**extras):
        return {'action':'schedule','payload':{'name':name,'kind':'expense','amount':amount,'account':self.a,'monthday':monthday,'start':'2026-10-02'},**extras}
    def key(self,text):return re.search(r'/confirm ([a-f0-9]{8})',text)[1]

    def test_monthly_list_never_becomes_actual_expense(self):
        cli=FakeCLI(('','stage',{'data':{'kind':'expense','amount':'100.00','date':'2026-10-20','account':self.a}}))
        a=Assistant(self.l,cli)
        answer=a.handle(OWNER,'Планируемые ежемесячные траты','tg:1')
        self.assertNotIn('/confirm',answer)
        self.assertEqual(self.l.snapshot()['operations'],[])
        self.assertTrue(intake.context(self.l,OWNER)['original_messages'])

    def test_incomplete_intake_persists_and_asks_one_question(self):
        payload={'items':[{'name':'Платёж','amount':'100.00','monthday':None}],'questions':['Какого числа первый платёж?','Какой счёт?']}
        reply=intake.save(self.l,OWNER,payload)
        self.assertIn('Какого числа первый платёж?',reply);self.assertNotIn('Какой счёт?',reply)
        for i in range(15):self.assistant.remember(OWNER,'user','Уточнение '+str(i))
        cli=FakeCLI(('Продолжим','none',{}));Assistant(self.l,cli).handle(OWNER,'Двадцатого числа','tg:2')
        self.assertIn('Платёж',cli.prompts[0]);self.assertIn('INTAKE=',cli.prompts[0])

    def test_bulk_confirmation_is_atomic_and_requires_consent(self):
        payload={'items':[self.schedule('Аренда'),self.schedule('Связь','50.00')]}
        p=self.assistant.propose(OWNER,'batch',payload,'tg:list')
        self.assertEqual(self.l.snapshot()['rules'],[])
        self.assertIn('Фактических списаний не будет',p)
        self.assistant.handle(OWNER,'/confirm '+self.key(p),'tg:confirm')
        self.assertEqual(len(self.l.snapshot()['rules']),2)
        self.assertEqual(self.l.snapshot()['operations'],[])
        self.assertEqual(self.l.balances('2026-10-20')[self.a],100000)
        self.assertEqual({r['lead_days'] for r in self.l.snapshot()['rules']},{1})
        self.assistant.handle(OWNER,'/confirm '+self.key(p),'tg:again')
        self.assertEqual(len(self.l.snapshot()['rules']),2)

    def test_failure_rolls_back_entire_batch(self):
        invalid={'action':'loan','payload':{'name':'Плохой','principal':'1000.00','annual_rate':'12','payment':'0.00','first_due':'2026-10-20','monthday':20,'remaining_months':12}}
        with self.assertRaises(ValueError):intake.apply(self.l,OWNER,{'items':[self.schedule(),invalid]},'tg:bad')
        self.assertEqual(self.l.snapshot()['rules'],[])
        self.assertEqual(self.l.snapshot()['loans'],[])

    def test_six_loans_are_six_obligations_not_twelve(self):
        items=[]
        for i in range(6):
            items+=[self.schedule('Кредит '+str(i)),{'action':'loan','payload':{'name':'Кредит '+str(i),'principal':'1000.00','annual_rate':'12','payment':'100.00','first_due':'2026-10-20','monthday':20,'remaining_months':12}}]
        intake.apply(self.l,OWNER,{'items':items},'tg:six')
        intake.apply(self.l,OWNER,{'items':items},'tg:six')
        self.assertEqual(len(self.l.snapshot()['rules']),6);self.assertEqual(len(self.l.snapshot()['loans']),6)
        self.assertEqual(self.l.forecast('2026-10-01','2026-10-31')['days'][-1]['total'],40000)
        self.assertEqual(len(self.l.calendar('2026-10-01','2026-10-31')),6)
        self.assertEqual(self.l.snapshot()['operations'],[])

    def test_equal_names_and_amounts_do_not_prove_duplicate(self):
        item=self.schedule()
        with self.assertRaises(ValueError):intake.apply(self.l,OWNER,{'items':[item,item]},'tg:ambiguous')
        self.assertEqual(self.l.snapshot()['rules'],[])
        distinct={**item,'distinct':True}
        intake.apply(self.l,OWNER,{'items':[item,distinct]},'tg:distinct')
        self.assertEqual(len(self.l.snapshot()['rules']),2)

    def test_explicit_existing_identity_reuses_plan(self):
        original=intake.apply(self.l,OWNER,{'items':[self.schedule()]},'tg:first')['items'][0]['id']
        with self.assertRaises(ValueError):intake.apply(self.l,OWNER,{'items':[self.schedule()]},'tg:unclear')
        intake.apply(self.l,OWNER,{'items':[self.schedule(existing_id=original)]},'tg:known')
        self.assertEqual(len(self.l.snapshot()['rules']),1)

    def test_totals_are_separate_and_mismatch_requires_user_choice(self):
        draft={'items':[{'name':'Строка 1'},{'name':'Строка 2'}],
               'lines':[{'category':'Обязательства','amount':'100.00'},{'category':'Обязательства','amount':'200.00'}],
               'category_totals':[{'category':'Обязательства','amount':'400.00'}],'questions':[]}
        intake.save(self.l,OWNER,draft)
        payload={'items':[self.schedule('Строка 1','100.00',category='Обязательства'),self.schedule('Строка 2','200.00',category='Обязательства')]}
        with self.assertRaises(ValueError):intake.apply(self.l,OWNER,payload,'tg:before-choice')
        self.assertEqual(intake.selected_basis('считать отдельные строки, итоги не прибавлять'),'lines')
        intake.choose(self.l,OWNER,'lines')
        intake.apply(self.l,OWNER,payload,'tg:after-choice')
        self.assertEqual(sum(r['amount'] for r in self.l.snapshot()['rules']),30000)
        self.assertEqual(self.l.snapshot()['operations'],[])

    def test_aggregate_and_details_cannot_be_added_together(self):
        payload={'items':[self.schedule('Деталь',category='А',role='line'),self.schedule('Итого',category='А',role='aggregate')]}
        with self.assertRaises(ValueError):intake.apply(self.l,OWNER,payload,'tg:double')
        self.assertEqual(self.l.snapshot()['rules'],[])

    def test_undated_plan_has_no_fake_due_date_or_reminder_but_affects_forecast(self):
        intake.apply(self.l,OWNER,{'items':[self.schedule('Несрочно',monthday=None)]},'tg:undated')
        calendar=self.l.calendar('2026-10-18','2026-11-30')
        self.assertEqual(len(calendar),2);self.assertTrue(all(e['date'] is None for e in calendar))
        self.assertEqual(self.l.queue_reminders('2026-10-18','2026-11-30'),[])
        f=self.l.forecast('2026-10-18','2026-11-30')
        self.assertEqual(f['days'][-1]['total'],80000)
        self.assertEqual(self.l.balances('2026-11-30')[self.a],100000)

    def test_future_undated_plan_is_not_repeated_before_start_month(self):
        item=self.schedule('Позже',monthday=None);item['payload']['start']='2026-12-10'
        intake.apply(self.l,OWNER,{'items':[item]},'tg:future-undated')
        events=self.l.calendar('2026-10-01','2026-12-31')
        self.assertEqual(len(events),1)

    def test_partial_credit_terms_survive_after_ready_obligations_confirmed(self):
        pending={'items':[{'name':'Кредит','principal':None}],'questions':['Какой остаток долга?']}
        intake.apply(self.l,OWNER,{'items':[self.schedule('Кредит')],'remaining_intake':pending},'tg:partial')
        self.assertEqual(intake.context(self.l,OWNER)['draft']['items'],pending['items'])
        self.assertEqual(len(self.l.snapshot()['rules']),1);self.assertEqual(self.l.snapshot()['loans'],[])


if __name__=='__main__':unittest.main()
