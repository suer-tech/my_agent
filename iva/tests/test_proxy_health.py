from pathlib import Path
import json
import os
import sqlite3
from datetime import datetime,timezone
import tempfile
import unittest
from deploy.telegram_proxy_health import tick,consumer_issue,deployment_guard


class Health(unittest.TestCase):
    def test_two_failures_restart_once_then_recover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';restarts=[]
            self.assertFalse(tick(lambda:False,lambda:restarts.append(1),1000,path)['restarted'])
            checks=iter([False,True])
            result=tick(lambda:next(checks),lambda:restarts.append(1),1060,path)
            self.assertTrue(result['healthy']);self.assertEqual(restarts,[1]);self.assertEqual(result['failures'],0)
    def test_restart_backoff_and_healthy_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';restarts=[]
            for clock in [1000,1060,1120,1180]:tick(lambda:False,lambda:restarts.append(1),clock,path)
            self.assertEqual(restarts,[1])
            result=tick(lambda:True,lambda:restarts.append(1),1240,path)
            self.assertTrue(result['healthy']);self.assertEqual(restarts,[1])
    def test_failed_restart_is_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';calls=[]
            def fail():calls.append(1);raise OSError('systemd unavailable')
            for clock in [1000,1060,1120]:tick(lambda:False,fail,clock,path)
            self.assertEqual(calls,[1]);self.assertEqual(json.loads(path.read_text())['last_restart'],1060)
    def test_idle_consumer_is_checked_only_when_network_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';calls=[]
            tick(lambda:False,lambda:None,1000,path,lambda t:calls.append(t))
            tick(lambda:True,lambda:None,1060,path,lambda t:(calls.append(t) or 'ok'))
            self.assertEqual(calls,[1060])
    def test_stale_poller_and_inflight_finance_are_distinguished(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data/iva-telegram').mkdir(parents=True)
            p=root/'data/iva-telegram/status.json'
            p.write_text(json.dumps({'heartbeat':datetime.fromtimestamp(1000,timezone.utc).isoformat(),'state':'processing'}))
            db=sqlite3.connect(root/'data/local-finance.sqlite3');db.execute('CREATE TABLE local_updates(state TEXT)');db.commit()
            self.assertIsNone(consumer_issue(1100,root,'active'))
            self.assertEqual(consumer_issue(1300,root,'active'),'stale_idle_poll')
            for state in ['processing','sending']:
                db.execute('DELETE FROM local_updates');db.execute('INSERT INTO local_updates VALUES(?)',(state,));db.commit()
                self.assertEqual(consumer_issue(1300,root,'active'),'owner_turn_in_progress')
            db.close()
            (root/'data/iva-telegram/stop').touch()
            self.assertIsNone(consumer_issue(1300,root,'failed'))
    def test_intentional_stop_never_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(consumer_issue(1300,Path(tmp),'inactive'))
    @unittest.skipIf(os.name=='nt','Linux deployment flock')
    def test_deployment_lock_prevents_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            with deployment_guard(root) as first:
                self.assertTrue(first)
                with deployment_guard(root) as second:self.assertFalse(second)
            with deployment_guard(root) as third:self.assertTrue(third)


if __name__=='__main__':unittest.main()
