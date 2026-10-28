from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from deploy.resource_monitor import tick,level,thresholds,MIB,GIB

NORMAL={'disk_total':20*GIB,'disk_free':10*GIB,'memory_total':2*GIB,'memory_available':900*MIB,'swap_total':2*GIB,'swap_free':GIB}


class Monitor(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'state.json';self.sent=[]
    def tearDown(self):self.tmp.cleanup()
    def check(self,metrics,clock):return tick(self.path,lambda:metrics,lambda text:(self.sent.append(text) or len(self.sent)),clock)
    def test_normal_sends_nothing(self):
        self.assertFalse(self.check(NORMAL,1000)['alert']);self.assertEqual(self.sent,[])
    def test_low_memory_warns_and_not_every_three_minutes(self):
        low={**NORMAL,'memory_available':220*MIB}
        self.assertTrue(self.check(low,1000)['delivered'])
        self.assertFalse(self.check(low,1180)['alert']);self.assertFalse(self.check(low,1360)['alert'])
        self.assertEqual(len(self.sent),1)
        self.assertTrue(self.check(low,2800)['delivered'])
    def test_escalation_and_recovery(self):
        self.check({**NORMAL,'memory_available':220*MIB},1000)
        self.assertTrue(self.check({**NORMAL,'memory_available':100*MIB},1180)['delivered'])
        self.assertIn('критически',self.sent[-1]);self.check(NORMAL,1360)
        self.assertIn('снова в норме',self.sent[-1]);self.assertFalse(self.check(NORMAL,1540)['alert'])
    def test_threshold_hysteresis_and_memavailable(self):
        limits=thresholds(NORMAL)['memory']
        self.assertEqual(level(270*MIB,limits,'warning'),'warning')
        self.assertEqual(level(350*MIB,limits,'warning'),'normal')
        self.assertFalse(self.check({**NORMAL,'memory_free':5*MIB},1000)['alert'])
    def test_disk_and_memory_combined(self):
        self.check({**NORMAL,'disk_free':400*MIB,'memory_available':100*MIB},1000)
        self.assertEqual(len(self.sent),1);self.assertIn('Диск:',self.sent[0]);self.assertIn('ОЗУ:',self.sent[0])
    def test_restart_remembers_sent_warning(self):
        low={**NORMAL,'disk_free':GIB}
        self.check(low,1000);self.assertFalse(self.check(low,1180)['alert']);self.assertEqual(len(self.sent),1)
    def test_failed_notification_retries_without_model(self):
        low={**NORMAL,'memory_available':100*MIB}
        def fail(_):raise OSError('network')
        result=tick(self.path,lambda:low,fail,1000);self.assertFalse(result['delivered'])
        self.assertTrue(self.check(low,1180)['delivered']);self.assertEqual(len(self.sent),1)
    def test_full_disk_uses_runtime_state_and_still_alerts(self):
        from deploy.resource_monitor import save
        runtime=Path(self.tmp.name)/'runtime.json';low={**NORMAL,'disk_free':0}
        def persist(path,value):
            if path==self.path:raise OSError('disk full')
            save(path,value)
        with patch('deploy.resource_monitor.save',side_effect=persist):
            first=tick(self.path,lambda:low,lambda text:(self.sent.append(text) or 1),1000,fallback=runtime)
            second=tick(self.path,lambda:low,lambda text:(self.sent.append(text) or 1),1180,fallback=runtime)
        self.assertTrue(first['delivered']);self.assertFalse(second['alert']);self.assertEqual(len(self.sent),1)


if __name__=='__main__':unittest.main()
