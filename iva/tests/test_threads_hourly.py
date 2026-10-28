import tempfile,unittest,sqlite3,time
from pathlib import Path
from unittest.mock import patch
from iva_integration import threads_hourly as w

class HourlySafety(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(w,'ROOT',Path(self.tmp.name));self.patch.start()
 def tearDown(self):self.patch.stop();self.tmp.cleanup()
 def test_freshness(self):
  self.assertTrue(w.fresh(9950,10000));self.assertTrue(w.fresh(2700,10000));self.assertFalse(w.fresh(2700,10000,max_age=7200));self.assertFalse(w.fresh(10001,10000));self.assertFalse(w.fresh(None,10000))
 def test_disclosure_and_one_link(self):
  self.assertTrue(w.valid_comment('Если ищете на hh, я делаю расширение. '+w.STORE))
  for x in ['Просто попробуйте '+w.STORE,'Я делаю расширение '+w.STORE+' https://evil.test', 'Я делаю '+('a'*501)+w.STORE]:self.assertFalse(w.valid_comment(x))
 def test_no_daily_cap_and_at_most_one_per_clock_hour(self):
  db=w.db_open()
  for i in range(5):db.execute("INSERT INTO posts(url,state,attempt_at) VALUES (?,'sent',?)",(str(i),1000+i*3600))
  db.commit()
  self.assertFalse(w.hourly_attempted(db,20000))
  db.execute("INSERT INTO posts(url,state,attempt_at) VALUES ('current','uncertain',20000)");db.commit()
  self.assertTrue(w.hourly_attempted(db,20001));self.assertFalse(w.hourly_attempted(db,21600));db.close()
 def test_unique_post_survives_reopen(self):
  db=w.db_open();db.execute("INSERT INTO posts(url,state,attempt_at) VALUES ('https://www.threads.com/@x/post/a','sending',?)",(time.time(),));db.commit();db.close();db=w.db_open()
  with self.assertRaises(sqlite3.IntegrityError):db.execute("INSERT INTO posts(url,state) VALUES ('https://www.threads.com/@x/post/a','new')")
  db.close()
 def test_only_public_threads_urls(self):
  for url in ['https://evil.test/@a/post/b','https://www.threads.com/messages','http://threads.com/@a/post/b']:
   with self.assertRaises(ValueError):w.canonical(url)
 def test_paused_step_does_not_touch_browser(self):
  w.write('hourly-launch.json',{'enabled':False,'pending':True});w.step(object());self.assertFalse((w.ROOT/'hourly.sqlite3').exists())
 def test_interrupted_publication_becomes_uncertain_without_replay(self):
  w.write('hourly-launch.json',{'enabled':True,'pending':True});db=w.db_open();db.execute("INSERT INTO cycles(started,state) VALUES (?,'active')",(time.time(),));db.execute("INSERT INTO posts(url,state) VALUES ('https://www.threads.com/@a/post/b','sending')");db.commit();db.close()
  with patch.object(w,'finish') as finish:w.step(object());self.assertEqual(finish.call_args[0][2],'error')
  db=w.db_open();self.assertEqual(db.execute('SELECT state FROM posts').fetchone()[0],'uncertain');db.close()

if __name__=='__main__':unittest.main()
