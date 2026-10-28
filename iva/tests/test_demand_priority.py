import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from iva_integration import research_priority as priority
from iva_integration.scheduled_tasks import Store
from iva_integration.scheduled_worker import tick


class PriorityClient(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        priority._held=None;self.addCleanup(setattr,priority,'_held',None)
        self.status=patch.object(priority,'STATUS',Path(self.temp.name)/'status.json')
        self.status.start();self.addCleanup(self.status.stop)
        self.store=Store(Path(self.temp.name)/'schedule.sqlite3');self.addCleanup(self.store.close)
        self.clock=datetime(2026,10,25,9,tzinfo=timezone.utc)
        spec={'title':'Research','task':'Study demand','kind':'demand_research','cron':'0 * * * *'}
        self.job=self.store.execute({'action':'create',**spec},'owner:priority',clock=self.clock)['job']['id']

    def lease(self):
        priority._held={'job_id':self.job,'cycle_id':'cycle','lease_token':'token'}

    def test_acquire_remembers_lease_and_status_does_not_expose_token(self):
        with patch.object(priority,'request',return_value={'ok':True,'granted':True,'job_id':self.job,'cycle_id':'cycle','lease_token':'token'}):
            result=priority.acquire(self.job,'cycle')
        self.assertTrue(result['granted']);self.assertEqual(priority._held['lease_token'],'token')
        self.assertNotIn('token',(Path(self.temp.name)/'status.json').read_text())

    def test_intermediate_cursor_keeps_lease_and_cancel_releases_it(self):
        self.lease()
        self.store.db.execute('UPDATE scheduled_jobs SET cursor=? WHERE id=?',(json.dumps({'research_lease_token':'token','demand_cycle_id':'cycle'}),self.job))
        with patch.object(priority,'request',return_value={'ok':True}) as request:
            priority.reconcile(self.store);request.assert_not_called()
            self.store.db.execute("UPDATE scheduled_jobs SET state='paused' WHERE id=?",(self.job,))
            priority.reconcile(self.store)
        self.assertEqual(request.call_args.args[0]['action'],'research-release');self.assertIsNone(priority._held)

    def test_failed_release_retries_without_losing_ownership(self):
        self.lease()
        with patch.object(priority,'request',side_effect=[OSError(),{'ok':True}]) as request:
            priority.reconcile(self.store);self.assertIsNotNone(priority._held)
            priority.reconcile(self.store);self.assertIsNone(priority._held)
        self.assertEqual(request.call_count,2)

    def test_unexpected_research_exception_clears_hold_after_committed_cursor(self):
        self.lease()
        cursor={'research_lease_token':'token','demand_cycle_id':'cycle','demand_pending_result':{'ok':True,'notify':True,'text':'saved finding'}}
        self.store.db.execute('UPDATE scheduled_jobs SET cursor=?,next_at=? WHERE id=?',(json.dumps(cursor),self.clock.isoformat(),self.job))
        def released(payload):
            row=self.store.rows('SELECT cursor FROM scheduled_jobs WHERE id=?',(self.job,))[0]
            saved=json.loads(row['cursor'])
            self.assertNotIn('research_lease_token',saved)
            self.assertEqual(saved['demand_cycle_id'],'cycle')
            self.assertIn('demand_pending_result',saved)
            return {'ok':True}
        with patch.object(priority,'acquire',return_value={'ok':True,'granted':True,'lease_token':'token','cycle_id':'cycle'}),patch('iva_integration.scheduled_worker._run_demand_step',side_effect=RuntimeError('unforeseen')),patch.object(priority,'request',side_effect=released):
            tick(self.store,clock=self.clock)
        self.assertIsNone(priority._held)
        run=self.store.rows('SELECT result FROM scheduled_runs')[0]
        self.assertEqual(json.loads(run['result'])['status'],'resource_blocked')


if __name__=='__main__':unittest.main()
