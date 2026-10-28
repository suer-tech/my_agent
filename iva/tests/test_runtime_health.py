import unittest
from datetime import datetime,timezone
from iva_integration.runtime_health import issues


class Health(unittest.TestCase):
    def test_busy_turn_gets_its_full_bounded_time(self):
        stamp=datetime.fromtimestamp(1000,timezone.utc).isoformat()
        current=datetime.fromtimestamp(1500,timezone.utc).isoformat()
        self.assertEqual(issues(1500,{'state':'processing','heartbeat':stamp},current,['done'],{'gateway':True}),{})
        self.assertIn('telegram',issues(1500,{'state':'polling','heartbeat':stamp},current,[],{}))
    def test_only_repeated_failures_trigger_reply_alert(self):
        stamp=datetime.now(timezone.utc).isoformat();now=datetime.now(timezone.utc).timestamp()
        self.assertNotIn('replies',issues(now,{'heartbeat':stamp},stamp,['failed','done','failed'],{}))
        self.assertIn('replies',issues(now,{'heartbeat':stamp},stamp,['failed']*3,{}))
