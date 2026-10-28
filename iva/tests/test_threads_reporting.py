import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from iva_integration import threads_reporting as r, threads_inbox as inbox, threads_hourly as worker
from iva_integration.threads_alerts import Monitor


class Reporting(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'development').mkdir()
        self.now=datetime(2026,10,23,22,tzinfo=r.TZ).timestamp()
        for directory in [self.root,self.root/'development']:worker.db_open(directory).close()
    def tearDown(self):self.tmp.cleanup()
    def post(self,name,state,url='root',reply=None,when=None):
        with closing(sqlite3.connect(self.root/('development' if name=='development' else '')/'hourly.sqlite3')) as db, db:
            db.execute('INSERT INTO posts(url,state,reply_url,attempt_at) VALUES (?,?,?,?)',(url,state,reply,when or self.now-100))
    def test_only_confirmed_and_cross_campaign_dedupe(self):
        self.post('extension','sent',reply='reply');self.post('development','sent',reply='reply')
        self.post('development','uncertain','other');self.post('development','sent','missing-link')
        s=r.summarize(self.root,0,self.now)
        self.assertEqual(s['confirmed'],1);self.assertEqual(s['uncertain'],2)
    def test_time_window_and_error_cycles(self):
        self.post('extension','sent',reply='r',when=self.now-90000)
        with closing(sqlite3.connect(self.root/'hourly.sqlite3')) as db, db:
            db.execute("INSERT INTO cycles(state,finished) VALUES ('error',?)",(self.now-10,))
        s=r.summarize(self.root,self.now-86400,self.now)
        self.assertEqual(s['confirmed'],0);self.assertEqual(s['error_cycles'],1)
    def test_daily_period_and_idempotence(self):
        self.post('extension','sent',reply='r',when=self.now-7200)
        text=r.daily(self.root,self.now);r.daily(self.root,self.now+1)
        self.assertIn('Всего подтверждено: 1',text)
        m=Monitor(self.root)
        self.assertEqual(m.db.execute('SELECT count(*) FROM outbox').fetchone()[0],1);m.close()
        status=r.refresh(self.root,self.now)
        self.assertEqual(status['today']['confirmed'],1)
    def test_incoming_dedupe_change_and_read_state(self):
        def snap(unread,fp):return {'items':[{'url':'https://www.threads.com/messages/123/','unread':unread,'fingerprint':fp,'sender':'Someone'}]}
        self.assertEqual(inbox.store(snap(False,'a'),self.root,1),0)
        self.assertEqual(inbox.store(snap(True,'b'),self.root,2),1)
        self.assertEqual(inbox.store(snap(True,'b'),self.root,3),0)
        self.assertEqual(inbox.store(snap(True,'c'),self.root,4),1)
        m=Monitor(self.root)
        texts=[x[0] for x in m.db.execute('SELECT text FROM outbox')]
        self.assertEqual(len(texts),2);m.close()
    def test_enqueue_does_not_change_inflight_delivery(self):
        m=Monitor(self.root)
        with m.db:m.db.execute("INSERT INTO outbox VALUES ('x','text','sending',NULL,1)")
        inbox.store({'items':[]},self.root,2);r.daily(self.root,self.now)
        self.assertEqual(m.db.execute("SELECT state FROM outbox WHERE id='x'").fetchone()[0],'sending');m.close()
    def test_failed_scan_does_not_claim_empty(self):
        class Context:pages=[]
        from unittest.mock import patch
        with patch.object(inbox,'wake'):
            s=inbox.check(Context(),self.root,force=True)
        self.assertEqual(s['state'],'error');self.assertNotIn('visible_conversations',s)


if __name__=='__main__':unittest.main()
