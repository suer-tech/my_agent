import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

DEPLOY=Path(__file__).resolve().parents[1]/'deploy'
if sys.platform=='win32': raise unittest.SkipTest('Linux browser coordinator integration')
sys.path.insert(0,str(DEPLOY))
import browser_turn as turn
import browser_coordinator as coordinator_module
from browser_coordinator import Coordinator


class TurnsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'checkpoints').mkdir();self.live=[];self.calls=[];self.now=1000
        self.c=Coordinator(self.root,live=lambda:self.live,command=self.command,clock=lambda:self.now)

    def tearDown(self): self.temp.cleanup()

    def command(self,*args):
        self.calls.append(args)
        return subprocess.CompletedProcess(args,0)

    def test_switch_waits_for_owner_exit_and_rotates(self):
        self.c.state.update(queue=['max-manual','threads'],current='max-manual',since=0)
        self.c.handle({'action':'switch','task':'threads'})
        self.live=['max-manual'];self.c.tick()
        self.assertTrue(json.loads((self.root/'grant.json').read_text())['yield'])
        self.assertFalse(self.calls)
        self.assertEqual(self.c.state['queue'],['threads','max-manual'])
        self.live=[];self.c.tick()
        self.assertEqual(self.c.state['current'],'threads')
        self.assertIn(('start','--no-block','threads-browser.service'),self.calls)

    def test_unmanaged_owner_blocks_start(self):
        self.c.state['queue']=['threads'];self.live=['max-worker']
        self.c.tick();self.assertFalse(self.calls)
        self.assertIsNotNone(self.c.state['blocked'])

    def test_interactive_windows_do_not_rotate_even_after_hours(self):
        self.c.state.update(queue=['max-manual','threads'],current='threads',since=0)
        self.now=100000;self.live=['threads'];self.c.tick()
        self.assertFalse(self.calls);self.assertFalse(self.c.yielding)

    def test_only_competing_background_jobs_trigger_timed_yield(self):
        self.c.work=lambda task:task in ('max-worker','threads')
        self.c.state.update(queue=['max-worker'],current='max-worker',since=0)
        self.live=['max-worker'];self.c.tick();self.assertFalse(self.c.yielding)
        self.c.state['queue'].append('threads');self.c.tick();self.assertTrue(self.c.yielding)

    def test_pause_releases_at_boundary_without_systemctl_stop(self):
        self.c.state.update(current='max-manual',queue=['max-manual'],since=1000)
        self.live=['max-manual'];self.c.handle({'action':'pause'});self.c.tick()
        self.assertFalse(self.calls)
        self.assertTrue(json.loads((self.root/'grant.json').read_text())['yield'])

    def test_restart_retains_pending_safe_yield(self):
        self.c.state.update(current='max-manual',queue=['threads','max-manual'],since=0)
        self.c.grant('max-manual',True);self.c.save()
        recovered=Coordinator(self.root,live=lambda:[],command=self.command,clock=lambda:1000)
        recovered.tick();self.assertEqual(recovered.state['current'],'threads')

    def test_cooperative_cursor_persists_before_exit_and_reloads(self):
        with patch.object(turn,'STATE',self.root),patch.object(turn,'TASK','max-worker'),patch.object(turn,'YIELDED',False),patch.object(turn,'launch_key',return_value='same-launch'):
            turn.atomic(self.root/'grant.json',{'task':'max-worker','yield':False})
            turn.record_completed('https://max.ru/group-a')
            self.assertTrue(turn.completed('https://max.ru/group-a'))
            turn.atomic(self.root/'grant.json',{'task':'max-worker','yield':True})
            with self.assertRaises(SystemExit):
                turn.checkpoint(next_url='https://max.ru/group-b',next_index=2,stage='before_link')
            saved=json.loads(turn.cursor_path().read_text())
            self.assertEqual(saved['next_index'],2);self.assertTrue(saved['yielded'])
            self.assertIn('https://max.ru/group-a',saved['completed_urls'])
            with patch.object(turn,'launch_key',return_value='different-launch'):
                self.assertFalse(turn.completed('https://max.ru/group-a'))

    def test_admission_kernel_lock_excludes_second_owner(self):
        if sys.platform=='win32': self.skipTest('Linux flock only')
        lock=self.root/'owner.lock';lock.touch()
        turn.atomic(self.root/'grant.json',{'task':'threads','yield':False})
        with patch.object(turn,'STATE',self.root):
            turn.admit('threads')
            script="import fcntl; f=open(%r,'r+'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"%str(lock)
            child=subprocess.run([sys.executable,'-c',script],capture_output=True)
            self.assertNotEqual(child.returncode,0)
            turn._lock.close()


class ResearchTurnsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'checkpoints').mkdir();(self.root/'owner.lock').touch()
        self.live=[];self.calls=[];self.now=1000;self.lock_free=True
        self.identities={123:{'pid':123,'boot_id':'boot-a','start_ticks':'10'},
                         456:{'pid':456,'boot_id':'boot-a','start_ticks':'20'}}
        self.work=lambda task:task in ('max-worker','threads')
        self.c=self.make()
        original_read=Path.read_text
        self.mem=patch.object(Path,'read_text',lambda path,*args,**kwargs:
            'MemAvailable: 2097152 kB\n' if str(path)=='/proc/meminfo' else original_read(path,*args,**kwargs))
        self.mem.start()
        original_json=coordinator_module.read
        self.json_read=patch.object(coordinator_module,'read',lambda path,default=None:
            original_json(path,default) if Path(path).is_relative_to(self.root) else
            {'enabled':True} if Path(path).name in ('worker_launch.json','ads_launch.json','broadcast_launch.json') else default)
        self.json_read.start()

    def tearDown(self):
        self.mem.stop()
        self.json_read.stop()
        if self.c._research_lock is not None:self.c._research_lock.close()
        self.temp.cleanup()

    def make(self):
        return Coordinator(self.root,live=lambda:self.live,command=self.command,clock=lambda:self.now,
                           work=lambda task:self.work(task),process=lambda pid:self.identities.get(pid),
                           lock_available=lambda:self.lock_free)

    def command(self,*args):
        self.calls.append(args)
        return subprocess.CompletedProcess(args,0)

    def acquire(self,pid=123,job='job-a',cycle='cycle-a'):
        return self.c.handle({'action':'research-acquire','job_id':job,'cycle_id':cycle},peer_pid=pid)

    def release(self,token,pid=123,job='job-a',cycle='cycle-a'):
        return self.c.handle({'action':'research-release','job_id':job,'cycle_id':cycle,'lease_token':token},peer_pid=pid)

    def test_research_blocks_starts_without_mutating_queue_or_owner_pause(self):
        self.c.state.update(queue=['threads','max-worker'],enabled=False)
        result=self.acquire()
        self.assertTrue(result['granted']);self.assertEqual(result['status'],'active')
        self.c.tick();self.assertEqual(self.calls,[])
        self.assertEqual(self.c.state['queue'],['threads','max-worker'])
        self.assertFalse(self.c.state['enabled'])
        self.release(result['lease_token'])
        self.assertFalse(self.c.state['enabled']);self.assertEqual(self.calls,[])

    def test_release_resumes_original_queue_immediately_and_keeps_pause_ledger(self):
        self.c.state['queue']=['threads','max-worker']
        result=self.acquire();self.now=4600
        self.release(result['lease_token'])
        self.assertIn(('start','--no-block','threads-browser.service'),self.calls)
        self.assertEqual(self.c.state['queue'],['threads','max-worker'])
        ledger=json.loads((self.root/'research-pauses.json').read_text())
        self.assertEqual(ledger['intervals'],[[1000,4600]]);self.assertIsNone(ledger['active_since'])

    def test_live_research_is_not_rotated_after_hours(self):
        result=self.acquire();self.c.state['queue']=['threads']
        self.now=100000;self.c.tick()
        self.assertEqual(self.c.research_status()['status'],'active')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.acquire()['lease_token'],result['lease_token'])

    def test_background_yields_at_checkpoint_before_grant_without_reordering(self):
        self.c.state.update(current='max-worker',queue=['max-worker','threads'],since=0)
        self.live=['max-worker'];self.c.grant('max-worker')
        waiting=self.acquire()
        self.assertFalse(waiting['granted']);self.assertEqual(waiting['status'],'waiting_background')
        self.assertTrue(json.loads((self.root/'grant.json').read_text())['yield'])
        self.assertEqual(self.calls,[]);self.assertEqual(self.c.state['queue'],['max-worker','threads'])
        turn.atomic(self.root/'checkpoints/max-worker.json',{'yielded':True,'next_url':'https://max.ru/next'})
        self.live=[];self.c.tick()
        self.assertEqual(self.c.research_status()['status'],'active')
        self.assertIsNone(self.c.state['current'])
        self.assertEqual(self.c.state['queue'],['max-worker','threads'])

    def test_interactive_browser_is_not_asked_to_exit(self):
        self.c.state.update(current='max-manual',queue=['max-manual','threads'],since=0)
        self.live=['max-manual'];self.c.grant('max-manual');self.work=lambda task:True
        waiting=self.acquire()
        self.assertEqual(waiting['status'],'waiting_interactive')
        self.assertFalse(json.loads((self.root/'grant.json').read_text())['yield'])
        self.assertEqual(self.calls,[])

    def test_grant_waits_for_owner_lock_even_when_unit_is_gone(self):
        self.lock_free=False
        waiting=self.acquire()
        self.assertEqual(waiting['status'],'waiting_owner_lock')
        self.lock_free=True;self.c.tick()
        self.assertEqual(self.c.research_status()['status'],'active')

    def test_owner_can_release_interactive_window_while_research_waits(self):
        self.c.state.update(current='max-manual',queue=['max-manual','threads'],since=0)
        self.live=['max-manual'];self.c.grant('max-manual')
        self.assertEqual(self.acquire()['status'],'waiting_interactive')
        self.c.handle({'action':'switch','task':'threads'})
        self.c.tick()
        self.assertTrue(json.loads((self.root/'grant.json').read_text())['yield'])
        self.assertEqual(self.calls,[])
        self.live=[];self.c.tick()
        self.assertEqual(self.c.research_status()['status'],'active')
        self.assertEqual(self.calls,[])

    def test_dead_pid_or_pid_reuse_releases_after_grace_and_resumes(self):
        self.c.state['queue']=['threads'];self.acquire()
        self.identities[123]={'pid':123,'boot_id':'boot-a','start_ticks':'999'}
        self.now=1100;self.c.tick();self.assertEqual(self.calls,[])
        self.now=1219;self.c.tick();self.assertEqual(self.calls,[])
        self.now=1220;self.c.tick()
        self.assertEqual(self.c.research_status()['status'],'idle')
        self.assertIn(('start','--no-block','threads-browser.service'),self.calls)

    def test_same_job_can_recover_dead_owner_before_grace_with_new_token(self):
        original=self.acquire();self.identities.pop(123);self.now=1100
        recovered=self.acquire(pid=456)
        self.assertTrue(recovered['granted']);self.assertNotEqual(recovered['lease_token'],original['lease_token'])
        self.assertEqual(recovered['granted_at'],1000)
        with self.assertRaises(PermissionError):self.release(original['lease_token'],pid=456)
        self.assertTrue(self.release(recovered['lease_token'],pid=456)['released'])

    def test_wrong_owner_cannot_release_or_steal_and_status_hides_tokens(self):
        original=self.acquire()
        self.assertEqual(self.acquire(pid=456,job='other')['status'],'busy')
        with self.assertRaises(PermissionError):self.release(original['lease_token'],pid=456)
        public=json.dumps(self.c.status())
        self.assertNotIn(original['lease_token'],public);self.assertNotIn('start_ticks',public)
        with self.assertRaises(PermissionError):self.c.handle({'action':'research-acquire','job_id':'a'},peer_pid=999)

    def test_restart_preserves_lease_and_idempotent_release(self):
        original=self.acquire();self.c=self.make();self.now=5000
        recovered=self.acquire()
        self.assertEqual(recovered['lease_token'],original['lease_token'])
        self.assertTrue(recovered['granted'])
        self.release(recovered['lease_token'])
        duplicate=self.release(recovered['lease_token'])
        self.assertTrue(duplicate['duplicate'])

    def test_unknown_cycle_binds_without_changing_token(self):
        first=self.acquire(cycle=None)
        bound=self.acquire(cycle='actual-cycle')
        self.assertEqual(first['lease_token'],bound['lease_token'])
        self.assertEqual(bound['cycle_id'],'actual-cycle')

    def test_kernel_lock_is_held_for_whole_research_lease(self):
        self.c.lock_available=self.c.owner_lock_available
        result=self.acquire()
        script="import fcntl; f=open(%r,'r+'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"%str(self.root/'owner.lock')
        blocked=subprocess.run([sys.executable,'-c',script],capture_output=True)
        self.assertNotEqual(blocked.returncode,0)
        self.release(result['lease_token'])
        available=subprocess.run([sys.executable,'-c',script],capture_output=True)
        self.assertEqual(available.returncode,0)


if __name__=='__main__': unittest.main()
