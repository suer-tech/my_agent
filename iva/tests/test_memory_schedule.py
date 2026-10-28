from datetime import datetime,timezone,timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from iva_integration import memory_scheduler as scheduler
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from iva_integration.memory_scheduler import due,next_run,lock


class MemoryScheduleTests(unittest.TestCase):
    def test_local_four_am_boundary_and_catchup(self):
        zone=timezone(timedelta(hours=5))
        before=datetime(2026,10,20,3,59,tzinfo=zone)
        self.assertEqual(due(before),datetime(2026,10,19,4,tzinfo=zone))
        self.assertEqual(next_run(before),datetime(2026,10,20,4,tzinfo=zone))
        resumed=datetime(2026,10,23,11,tzinfo=zone)
        self.assertEqual(due(resumed),datetime(2026,10,23,4,tzinfo=zone))

    def test_exclusive_os_lock_released_on_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'memory.lock'
            with lock(path):
                with self.assertRaises(RuntimeError):
                    with lock(path):pass
            with lock(path):pass

    def test_successful_catchup_not_repeated(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scheduler,'STATUS',Path(temp)/'state.json'), patch.object(scheduler,'ensure_gateway'), patch.object(scheduler,'run_native',return_value={'exit_code':0}) as run, patch.object(scheduler,'maintenance',return_value=True):
            scheduler.tick()
            self.assertTrue(scheduler.state()['last_run_ok'])
            scheduler.tick()
            self.assertEqual(run.call_count,1)

    def test_failed_job_keeps_due_pending_and_defers_retry(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(scheduler,'STATUS',Path(temp)/'state.json'), patch.object(scheduler,'ensure_gateway'), patch.object(scheduler,'run_native',return_value={'exit_code':1}) as run, patch.object(scheduler,'maintenance') as maintenance:
            scheduler.tick()
            state=scheduler.state()
            self.assertFalse(state['last_run_ok'])
            self.assertNotIn('last_successful_due',state)
            self.assertGreater(datetime.fromisoformat(state['retry_after']),datetime.fromisoformat(state['last_started']))
            scheduler.tick()
            self.assertEqual(run.call_count,1)
            maintenance.assert_not_called()


if __name__=='__main__':unittest.main()
