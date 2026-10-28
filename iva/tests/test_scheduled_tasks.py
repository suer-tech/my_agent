import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from iva_integration.scheduled_tasks import Store,validate,next_time,cron_fields,execute_owner
from iva_integration.scheduled_worker import tick,read_tool,run_agent
from iva_integration.personal_mail import watch_poll

NOW=datetime(2026,10,22,10,tzinfo=timezone.utc)
SPEC={'title':'Test','task':'Check new mail','interval_minutes':180,'kind':'agent'}


class Schedules(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'jobs.sqlite3';self.store=Store(self.path)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def create(self,**values):return self.store.execute({'action':'create',**SPEC,**values},'owner:first',clock=NOW)['job']['id']
    def due(self,job):self.store.db.execute('UPDATE scheduled_jobs SET next_at=? WHERE id=?',(NOW.isoformat(),job))
    def test_interval_and_numeric_cron_timezone(self):
        self.assertEqual(next_time(validate(SPEC),NOW),NOW+timedelta(hours=3))
        self.assertEqual(next_time({'cron':'0 */3 * * *','timezone':'Asia/Yekaterinburg'},NOW),NOW+timedelta(hours=3))
        self.assertEqual(next_time({'cron':'30 9 * * 1-5','timezone':'Asia/Yekaterinburg'},NOW).astimezone(timezone(timedelta(hours=5))).hour,9)
        for value in ['* * * *','60 * * * *','*/0 * * * *','* * * * nope']:
            with self.assertRaises(ValueError):cron_fields(value)
        with self.assertRaises(ValueError):validate({**SPEC,'cron':'* * * * *'})
        with self.assertRaises(ValueError):validate({**SPEC,'interval_minutes':1})
    def test_restart_duplicate_create_and_no_overlap(self):
        job=self.create();again=self.store.execute({'action':'create',**SPEC},'owner:first',clock=NOW)
        self.assertTrue(again['duplicate']);self.assertEqual(again['job']['id'],job)
        self.due(job);claim=self.store.claim(NOW);other=Store(self.path)
        try:self.assertIsNone(other.claim(NOW))
        finally:other.close()
        self.store.close();self.store=Store(self.path);self.store.recover()
        self.assertIsNotNone(self.store.claim(NOW));self.assertEqual(self.store.rows("SELECT count(*) n FROM scheduled_runs WHERE state='interrupted'")[0]['n'],1)
    def test_changed_results_not_repeated_and_schedule_coalesces(self):
        job=self.create();self.due(job)
        result=lambda *_:{'ok':True,'notify':True,'text':'same'}
        tick(self.store,result,NOW);self.due(job);tick(self.store,result,NOW+timedelta(days=3))
        self.assertEqual(len(self.store.rows('SELECT * FROM scheduled_outbox')),1)
        self.assertGreater(self.store.rows('SELECT next_at FROM scheduled_jobs')[0]['next_at'],(NOW+timedelta(days=3)).isoformat())
    def test_delivery_uncertain_never_automatically_repeated(self):
        job=self.create();self.due(job);tick(self.store,lambda *_:{'ok':True,'notify':True,'text':'hi'},NOW)
        sent=[]
        def sender(text):sent.append(text);raise OSError('lost acknowledgement')
        self.store.deliver(sender);self.store.recover();self.store.deliver(sender)
        self.assertEqual(len(sent),1);self.assertEqual(len(self.store.status()['uncertain_deliveries']),1)
    def test_cancel_running_suppresses_pending_result(self):
        job=self.create();self.due(job);row,run=self.store.claim(NOW)
        self.store.execute({'action':'cancel','id':job},'owner:cancel',clock=NOW)
        self.store.finish(row,run,{'ok':True,'notify':True,'text':'stale'},NOW)
        self.assertEqual(self.store.rows('SELECT * FROM scheduled_outbox'),[])
    def test_pause_resume_update(self):
        job=self.create();self.store.execute({'action':'pause','id':job},'owner:pause',clock=NOW)
        self.due(job);self.assertIsNone(self.store.claim(NOW))
        self.store.execute({'action':'update','id':job,'cron':'0 9 * * *'},'owner:update',clock=NOW)
        result=self.store.execute({'action':'resume','id':job},'owner:resume',clock=NOW)
        self.assertEqual(result['job']['state'],'active');self.assertNotIn('interval_minutes',result['job'])
    def test_mail_creation_requires_live_baseline_and_no_old_mail(self):
        poll=lambda *_:{'ok':True,'cursor':{'last_uid':100,'uidvalidity':'7'}}
        data={'action':'create',**SPEC,'kind':'mail_watch','queries':['roseltorg']}
        with self.assertRaises(ValueError):self.store.execute(data,'owner:mail',clock=NOW)
        result=self.store.execute(data,'owner:mail',poll,NOW);job=result['job']['id']
        self.assertEqual(json.loads(self.store.rows('SELECT cursor FROM scheduled_jobs')[0]['cursor'])['last_uid'],100)
        calls=[]
        self.store.execute({'action':'update','id':job,'queries':['another']},'owner:change',lambda *args:(calls.append(args) or poll()),NOW)
        self.assertEqual(len(calls),1)
    def test_failed_mail_does_not_advance_cursor_and_backs_off(self):
        job=self.create();self.due(job)
        tick(self.store,lambda *_:{'ok':False,'error':'network'},NOW)
        row=self.store.rows('SELECT * FROM scheduled_jobs')[0]
        self.assertEqual(row['failures'],1);self.assertEqual(json.loads(row['cursor']),{})
        self.assertEqual(row['next_at'],(NOW+timedelta(minutes=5)).isoformat())
    def test_worker_rejects_writes_without_using_owner_session(self):
        for tool,payload in [('calendar',{'action':'add'}),('finance',{'action':'account'}),('web',{'action':'click'}),('shell',{})]:
            with self.assertRaises(ValueError):read_tool(tool,payload)
    def test_browser_read_navigates_to_requested_url(self):
        with patch('iva_integration.web_browser.browser_request',return_value={'ok':True}) as browser:
            read_tool('web',{'action':'read','url':'https://example.org'})
            self.assertEqual(browser.call_args.args[0]['action'],'open')
    def owner_ledger(self):
        from local_finance.assistant import Ledger
        ledger=Ledger(self.path)
        ledger.db.executescript('CREATE TABLE local_updates(id INTEGER,chat INTEGER,state TEXT,metadata TEXT);CREATE TABLE local_telegram_messages(id INTEGER,message_json TEXT);')
        message={'text':'Каждые три часа проверяй сайт и сообщай об изменениях','from':{'id':42},'chat':{'id':42,'type':'private'}}
        ledger.db.execute('INSERT INTO local_updates VALUES(1,42,?,?)',('processing','{}'))
        ledger.db.execute('INSERT INTO local_telegram_messages VALUES(1,?)',(json.dumps(message),));ledger.db.commit()
        return ledger,message
    def test_current_owner_creation_has_no_confirmation_and_is_idempotent(self):
        ledger,_=self.owner_ledger()
        try:
            result=execute_owner({'action':'create',**SPEC},ledger,42,'telegram:42:1:0')
            self.assertTrue(result['ok']);self.assertEqual(result['job']['state'],'active')
            again=execute_owner({'action':'create',**SPEC},ledger,42,'telegram:42:1:0')
            self.assertTrue(again['duplicate']);self.assertEqual(again['job']['id'],result['job']['id'])
            self.assertEqual(ledger.rows('SELECT count(*) n FROM operations')[0]['n'],0)
        finally:ledger.db.close()
    def test_forwarded_or_missing_owner_cannot_create_schedule(self):
        ledger,message=self.owner_ledger()
        try:
            message['forward_origin']={'type':'user'}
            ledger.db.execute('UPDATE local_telegram_messages SET message_json=?',(json.dumps(message),));ledger.db.commit()
            with self.assertRaises(PermissionError):execute_owner({'action':'create',**SPEC},ledger,42,'telegram:42:1:0')
            with self.assertRaises(PermissionError):execute_owner({'action':'create',**SPEC},ledger,42,None)
        finally:ledger.db.close()
    def test_agent_reads_before_result(self):
        class Fake:
            def __init__(self):self.i=0
            def structured(self,*args):
                self.i+=1
                return {'tool':'mail' if self.i==1 else 'finish','arguments_json':'{"action":"list"}','text':'Found letter' if self.i>1 else '', 'notify':True}
        with patch('iva_integration.scheduled_worker.read_tool',return_value={'ok':True,'messages':[{'subject':'New'}]}) as read:
            result=run_agent({**SPEC,'timezone':'Asia/Yekaterinburg'},'',Fake())
        self.assertTrue(result['ok']);self.assertTrue(result['notify']);read.assert_called_once()

    def test_false_unavailable_claim_retries_external_route(self):
        class Fake:
            def __init__(self):self.i=0
            def structured(self,prompt,schema):
                self.i+=1
                return {'tool':'mail' if self.i==2 else 'finish','arguments_json':'{"action":"list"}',
                        'text':'Почта недоступна' if self.i==1 else 'Проверено','notify':True}
        with patch('iva_integration.scheduled_worker.read_tool',return_value={'ok':True,'messages':[]}) as read:
            result=run_agent({**SPEC,'timezone':'Asia/Yekaterinburg'},'Старый ложный отказ',Fake())
        self.assertTrue(result['ok']);self.assertEqual(result['checked_tools'],['mail']);read.assert_called_once()

    def test_unchecked_refusal_never_counts_as_success(self):
        class Fake:
            def structured(self,*args):return {'tool':'finish','arguments_json':'{}','text':'Недоступно','notify':True}
        with patch('iva_integration.scheduled_worker.read_tool') as read:
            result=run_agent({**SPEC,'timezone':'Asia/Yekaterinburg'},'',Fake())
        self.assertFalse(result['ok']);read.assert_not_called()

    def test_failed_tool_never_counts_as_completed_check(self):
        class Fake:
            def __init__(self):self.i=0
            def structured(self,*args):
                self.i+=1
                return {'tool':'mail' if self.i==1 else 'finish','arguments_json':'{"action":"list"}','text':'Ошибка','notify':True}
        with patch('iva_integration.scheduled_worker.read_tool',return_value={'ok':False,'error':'offline'}):
            result=run_agent({**SPEC,'timezone':'Asia/Yekaterinburg'},'',Fake())
        self.assertFalse(result['ok'])


class MailCursor(unittest.TestCase):
    def test_new_uids_and_sender_filter(self):
        class Mail:
            def response(self,key):return 'OK',[b'104']
            def uid(self,*args):return 'OK',[b'100 101 102 103']
            def logout(self):pass
        with patch('iva_integration.personal_mail.credentials',return_value={}),patch('iva_integration.personal_mail.selected',return_value='7'),patch('iva_integration.personal_mail.fetch',side_effect=lambda c,uid:('',b'From: Roseltorg <notify@roseltorg.ru>\nSubject: Tender\n',90)):
            spec={'folder':'INBOX','queries':['roseltorg']}
            first=watch_poll(spec,{},connector=lambda _:Mail())
            self.assertEqual(first['cursor']['last_uid'],103);self.assertEqual(first['messages'],[])
            result=watch_poll(spec,{'last_uid':100,'uidvalidity':'7'},connector=lambda _:Mail())
            self.assertEqual([m['uid'] for m in result['messages']],['101','102','103'])
            self.assertEqual(result['cursor']['last_uid'],103)
            failed=watch_poll(spec,{'last_uid':100,'uidvalidity':'old'},connector=lambda _:Mail())
            self.assertFalse(failed['ok']);self.assertNotIn('cursor',failed)


if __name__=='__main__':unittest.main()
