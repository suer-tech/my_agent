import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

from iva_integration import threads_browser_rpc as browser
if not Path('/opt/iva').exists():raise unittest.SkipTest('Server SQLite/fcntl test')
from iva_integration import threads_test as worker


class ThreadsTest(unittest.TestCase):
    def test_urls_cannot_open_private_messages_or_other_sites(self):
        for url in ['https://evil.test/','https://www.threads.com/messages','https://www.threads.com/?token=abc','https://www.threads.com/@name/post/id/media']:
            with self.assertRaises(ValueError):browser.allowed_url(url)
        self.assertEqual(browser.allowed_url('https://www.threads.com/@name/post/id'),'https://www.threads.com/@name/post/id')

    def test_one_test_and_post_have_durable_unique_records(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(worker,'ROOT',Path(directory)):
            db=worker.database()
            with db:db.execute("INSERT INTO runs(id,state,post_url) VALUES ('a','sending','https://www.threads.com/@x/post/a')")
            db.close();db=worker.database()
            self.assertEqual(db.execute("SELECT state FROM runs WHERE id='a'").fetchone()[0],'sending')
            with self.assertRaises(Exception):
                with db:db.execute("INSERT INTO runs(id,state,post_url) VALUES ('b','created','https://www.threads.com/@x/post/a')")
            db.close()

    def test_timeout_after_submit_cannot_trigger_a_second_send(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(worker,'ROOT',Path(directory)):
            db=worker.database()
            with db:db.execute('INSERT INTO runs(id,state,post_url,comment) VALUES (?,?,?,?)',(worker.RUN,'drafted','https://www.threads.com/@x/post/a','Я делаю тест '+worker.STORE))
            db.close();submits=[]
            snap={'url':'https://www.threads.com/@x/post/a','editors':[{}],'buttons':[{'text':'Post'}],'text':'test','links':[]}
            def fake_rpc(action,**data):
                if action=='click':submits.append(data);raise TimeoutError('Acknowledgement lost')
                return snap
            with patch.object(worker,'rpc',side_effect=fake_rpc),patch.object(sys,'argv',['test','publish']):
                with self.assertRaises(TimeoutError):worker.main()
                with self.assertRaises(RuntimeError):worker.main()
            self.assertEqual(len(submits),1)
            db=worker.database();self.assertEqual(db.execute('SELECT state FROM runs').fetchone()[0],'uncertain');db.close()


if __name__=='__main__':unittest.main()
