import sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from iva_integration import threads_hourly as w,threads_development as dev
from iva_integration.threads_alerts import Monitor,detect

class DevelopmentTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'development').mkdir()
  self.p=patch.object(w,'ROOT',self.root);self.p.start()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def test_config_is_separate_half_hour_without_age_limit(self):
  cfg=dev.config();self.assertEqual(cfg['root'],self.root/'development');self.assertEqual(cfg['interval'],1800);self.assertIsNone(cfg['max_age'])
  self.assertEqual(w.default_config()['interval'],3600);self.assertNotEqual(cfg['url'],w.STORE)
 def test_portfolio_and_disclosure_are_required_without_extension_link(self):
  cfg=dev.config();args={'url':cfg['url'],'disclosures':cfg['disclosures']}
  self.assertTrue(w.valid_comment('Я разработчик: могу обсудить AI-агента для вашего процесса. '+dev.PORTFOLIO,**args))
  self.assertFalse(w.valid_comment('Я разработчик, вот мой сайт '+w.STORE,**args))
  self.assertFalse(w.valid_comment('Рекомендую хорошего разработчика '+dev.PORTFOLIO,**args))
 def test_ai_priority_does_not_exclude_general_work(self):
  self.assertEqual(dev.priority('Нужен разработчик для внедрения ИИ и RAG'),1);self.assertEqual(dev.priority('Ищу разработчика сайта'),0)
 def test_cross_campaign_dedupe_covers_uncertain_and_sent(self):
  db=w.db_open();db.execute("INSERT INTO posts(url,state) VALUES ('https://www.threads.com/@x/post/a','uncertain')");db.commit();db.close()
  self.assertTrue(w.known_reply('https://www.threads.com/@x/post/a'));self.assertFalse(w.known_reply('https://www.threads.com/@x/post/b'))
 def test_finish_does_not_release_browser_with_other_campaign_pending(self):
  w.write('hourly-launch.json',{'enabled':True,'pending':True})
  cfg=dev.config();w.write('hourly-launch.json',{'enabled':True,'pending':True},root=cfg['root'])
  db=w.db_open(cfg['root']);db.execute("INSERT INTO cycles(id,state) VALUES (1,'active')");db.commit()
  request=Mock()
  with patch.dict('sys.modules',{'browser_turn':SimpleNamespace(request=request)}):
   w.finish(db,{'id':1},config=cfg);request.assert_not_called()
  self.assertFalse(w.read('hourly-launch.json',root=cfg['root'])['pending']);self.assertTrue(w.read('hourly-launch.json')['pending']);db.close()
 def test_dispatch_is_one_campaign_at_a_time(self):
  w.write('hourly-launch.json',{'enabled':True,'pending':True},root=dev.config()['root'])
  with patch.object(w,'step') as step:
   dev.step('single-context');self.assertEqual(step.call_count,1);self.assertEqual(step.call_args.kwargs['config']['name'],'development')
 def test_half_hour_slot_has_no_daily_limit(self):
  db=w.db_open(dev.config()['root'])
  for i in range(10):db.execute("INSERT INTO posts(url,state,attempt_at) VALUES (?,'sent',?)",(str(i),i*1800))
  db.commit();self.assertFalse(w.hourly_attempted(db,19000,1800));db.close()
 def test_verified_development_lead_notified_once(self):
  m=Monitor(self.root,lambda:1000);post={'url':'https://www.threads.com/@x/post/a','state':'sent','reply_url':'https://www.threads.com/@suer_stuff/post/b','attempt_at':1001}
  m.record_leads([post]);m.record_leads([post]);self.assertEqual(m.db.execute('SELECT count(*) FROM outbox').fetchone()[0],1);m.close()
 def test_preview_does_not_report_deliberately_unstarted_timer(self):
  data={'launch':{'enabled':True,'publish':False,'last_requested_at':1000},'report':{},'coordinator':{},'timer_unit':'threads-development.timer','interval':1800,'units':{'threads-development.timer':{'ActiveState':'inactive'}}}
  self.assertEqual(detect(data,3801,1000),{})
 def test_half_hour_timer_is_monitored_independently(self):
  data={'launch':{'enabled':True,'last_requested_at':1000},'report':{},'coordinator':{},'timer_unit':'threads-development.timer','interval':1800,'units':{'threads-development.timer':{'ActiveState':'active'}}}
  self.assertEqual(detect(data,3000,1000),{});self.assertIn('schedule',detect(data,3801,1000))

if __name__=='__main__':unittest.main()
