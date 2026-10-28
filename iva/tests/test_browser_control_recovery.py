import json
import unittest
from unittest.mock import MagicMock, patch

from iva_integration import browser_control


class RecoveryStatusIntegration(unittest.TestCase):
    def test_owner_status_includes_notification_delivery_without_changing_coordinator(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.return_value = b'{"ok":true,"owner":"research"}\n'
        evidence = {'checked_at': 100, 'stale': False, 'counts': {'sent': 1}}
        with patch.object(browser_control.socket, 'AF_UNIX', 1, create=True), \
                patch.object(browser_control.socket, 'socket', return_value=sock), \
                patch('iva_integration.recovery_notices.read_status', return_value=evidence):
            result = browser_control.execute({'action': 'status'})
        self.assertEqual(result['recovery_notices'], evidence)
        self.assertEqual(result['owner'], 'research')
        self.assertEqual(json.loads(sock.sendall.call_args.args[0]), {'action': 'status'})

    def test_mutating_commands_still_require_current_owner_instruction(self):
        with patch.object(browser_control.socket, 'socket') as socket:
            with self.assertRaises(PermissionError):
                browser_control.execute({'action': 'threads-run'})
            socket.assert_not_called()

    def test_guidance_distinguishes_intermediate_correction_and_real_recovery(self):
        self.assertIn('status.recovery_notices', browser_control.GUIDANCE)
        self.assertIn('not completion of the whole study', browser_control.GUIDANCE)
        self.assertIn('never claim a queued task is fixed', browser_control.GUIDANCE)


if __name__ == '__main__':
    unittest.main()
