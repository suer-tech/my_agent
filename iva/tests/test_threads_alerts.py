import tempfile,unittest
from pathlib import Path
from iva_integration.threads_alerts import Monitor,detect,category

class AlertsTest(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.monitor=Monitor(self.root,lambda:1000)
 def tearDown(self):self.monitor.close();self.tmp.cleanup()
 def issue(self):return {'cycle:login':{'code':'login','url':None}}
 def notice(self):
  with self.monitor.db:self.monitor.db.execute("INSERT INTO outbox VALUES ('dm:fixture','New incoming message','pending',NULL,1000)")
 def base(self):return {'launch':{'enabled':True,'pending':False,'last_requested_at':1000},'report':{'cycle':{'state':'done','finished':1000}},'coordinator':{'enabled':True},'units':{'threads-hourly.timer':{'ActiveState':'active'}}}
 def test_deduplicates_continuing_incident_across_restarts(self):
  self.monitor.sync(self.issue());self.monitor.sync(self.issue());self.monitor.close();self.monitor=Monitor(self.root,lambda:1000);self.monitor.sync(self.issue())
  self.assertEqual(self.monitor.db.execute('SELECT count(*) FROM incidents WHERE active=1').fetchone()[0],1);self.assertEqual(self.monitor.db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
 def test_delivery_ack_and_no_resend(self):
  self.notice();sent=[]
  self.monitor.deliver(lambda text:sent.append(text) or {'message_id':123});self.monitor.deliver(lambda text:sent.append(text))
  self.assertEqual(len(sent),1);self.assertEqual(self.monitor.db.execute('SELECT state FROM outbox').fetchone()[0],'sent')
 def test_lost_telegram_ack_is_uncertain_not_retried(self):
  self.notice();calls=[]
  def failed(text):calls.append(text);raise TimeoutError()
  self.monitor.deliver(failed);self.monitor.deliver(failed);self.assertEqual(len(calls),1)
  self.assertEqual(self.monitor.db.execute('SELECT state FROM outbox').fetchone()[0],'uncertain')
 def test_preflight_failure_keeps_pending(self):
  self.notice();self.monitor.deliver(lambda text:self.fail('must not send'),lambda:False)
  self.assertEqual(self.monitor.db.execute('SELECT state FROM outbox').fetchone()[0],'pending')
 def test_failure_recovery_and_recurrence_remain_internal(self):
  self.monitor.sync(self.issue());self.monitor.sync({});self.monitor.sync({});self.monitor.sync(self.issue())
  self.assertEqual(self.monitor.db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
 def test_pause_does_not_claim_recovery(self):
  self.monitor.sync(self.issue());self.monitor.sync({},notify_recovery=False)
  self.assertEqual(self.monitor.db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
 def test_empty_or_no_fit_cycle_is_not_error(self):self.assertEqual(detect(self.base(),1000,900),{})
 def test_login_ai_and_ui_are_distinguished(self):
  self.assertEqual(category('Threads login required'),'login');self.assertEqual(category('Codex CLI завершился'),'ai');self.assertEqual(category('Generated comment failed disclosure'),'interface');self.assertEqual(category('Unique submit button required'),'interface')
 def test_failed_cycle_notifies_but_older_error_is_not_replayed(self):
  data=self.base();data['report']['cycle']={'state':'error','finished':1001,'error':'Threads login required'}
  self.assertIn('cycle:login',detect(data,1002,1000));self.assertNotIn('cycle:login',detect(data,1002,1002))
 def test_uncertain_send_and_like_even_when_schedule_paused(self):
  data=self.base();data['launch']['enabled']=False;data['posts']=[{'url':'https://www.threads.com/@x/post/a','state':'uncertain'},{'url':'https://www.threads.com/@y/post/b','state':'sent','liked':-1,'attempt_at':1001}]
  self.assertEqual(len(detect(data,1002,1000)),2)
 def test_queue_wait_is_not_stall_and_oom_is_detected(self):
  data=self.base();data['launch']['pending']=True;data['coordinator']['current']='max-worker'
  self.assertEqual(detect(data,1100,900),{})
  self.assertIn('queue',detect(data,2300,900))
  data['units']['threads-browser.service']={'Result':'oom-kill','ActiveState':'failed'}
  self.assertIn('memory',detect(data,2300,900))
 def research_wait(self):
  data=self.base();data['launch']['pending']=True
  data['coordinator'].update(ok=True,current=None,research={'status':'active','active':True,'granted':True})
  data['memory_available_kb']=100
  data['units']['threads-browser.service']={'Result':'success','ActiveState':'inactive'}
  data['activity']={'state':'running','campaign':'extension','deadline_at':1100}
  return data
 def test_long_granted_research_pause_is_not_queue_memory_or_stall_failure(self):
  data=self.research_wait()
  self.assertEqual(detect(data,5000,900),{})
  # Even a stale current/activity snapshot must not turn a confirmed pause into a stall.
  data['coordinator']['current']='threads'
  self.assertEqual(detect(data,5000,900),{})
 def test_research_pause_preserves_actual_oom_uncertain_and_login_failures(self):
  data=self.research_wait();url='https://www.threads.com/@x/post/a'
  data['units']['threads-browser.service']={'Result':'oom-kill','ActiveState':'failed'}
  data['posts']=[{'url':url,'state':'uncertain'}]
  data['report']['cycle']={'state':'error','finished':1001,'error':'Threads login required'}
  issues=detect(data,5000,900)
  self.assertEqual(set(issues),{'memory','publication:'+url,'cycle:login'})
  data['units']['threads-browser.service']={'Result':'exit-code','ActiveState':'failed'}
  self.assertIn('browser',detect(data,5000,900))
 def test_ungranted_or_unverified_research_state_does_not_hide_wait_errors(self):
  data=self.research_wait();data['coordinator']['research']['granted']=False
  self.assertIn('queue',detect(data,5000,900));self.assertIn('memory',detect(data,5000,900))
  data['coordinator']['research']['granted']=True;data['coordinator']['ok']=False
  self.assertIn('queue',detect(data,5000,900))
 def test_sending_record_after_crash_never_replays(self):
  self.monitor.db.execute("INSERT INTO outbox VALUES ('test','test','sending',NULL,1000)");self.monitor.db.commit();self.monitor.close();self.monitor=Monitor(self.root,lambda:1001)
  self.monitor.deliver(lambda text:self.fail('must not replay'))
  self.assertEqual(self.monitor.db.execute('SELECT state FROM outbox').fetchone()[0],'uncertain')

 def test_inbox_operation_with_deadline_is_not_false_stall(self):
  data=self.base();data['launch']['pending']=True;data['coordinator']['current']='threads'
  data['activity']={'state':'running','campaign':'inbox','updated_at':1280,'deadline_at':1390}
  self.assertNotIn('stalled',detect(data,1300,900))
  self.assertNotIn('stalled',detect(data,1440,900))
  self.assertIn('stalled',detect(data,1451,900))
 def test_legacy_four_minute_gap_does_not_imply_stall(self):
  data=self.base();data['launch']['pending']=True;data['coordinator']['current']='threads'
  self.assertNotIn('stalled',detect(data,1241,900))
  self.assertIn('stalled',detect(data,1601,900))
 def test_shared_operation_creates_only_one_campaign_incident(self):
  data=self.base();data['launch']['pending']=True;data['coordinator']['current']='threads'
  data['activity']={'state':'running','campaign':'development','deadline_at':1100}
  data['campaign']='extension'
  self.assertNotIn('stalled',detect(data,1300,900))
  data['campaign']='development'
  self.assertIn('stalled',detect(data,1300,900))
 def test_pending_technical_notices_suppressed_but_dm_and_reports_preserved(self):
  with self.monitor.db:
   for ident in ['cycle:login:old','recovered:stalled:old','dm:new','daily:today','lead:post']:
    self.monitor.db.execute("INSERT INTO outbox VALUES (?,?,'pending',NULL,1000)",(ident,ident))
  sent=[]
  for _ in range(5):self.monitor.deliver(lambda text:sent.append(text) or {'message_id':123})
  self.assertEqual(sent,['dm:new','daily:today','lead:post'])
  self.assertEqual(self.monitor.db.execute("SELECT count(*) FROM outbox WHERE state='suppressed'").fetchone()[0],2)

if __name__=='__main__':unittest.main()
