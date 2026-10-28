import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy import verify_demand_branch as diagnostic
from iva_integration import research_priority


class DiagnosticResearchLease(unittest.TestCase):
    def setUp(self):
        self.state = {'job_id': 'job-fixture', 'cycle_id': 'cycle-fixture'}
        self.clock = [0.0]
        self.events = []
        self.acquire = Mock()
        self.release = Mock(return_value=True)
        patches = [
            patch.object(research_priority, 'acquire', self.acquire),
            patch.object(research_priority, 'release', self.release),
            patch.object(diagnostic.time, 'monotonic', side_effect=lambda: self.clock[0]),
            patch.object(diagnostic.time, 'sleep', side_effect=self.sleep),
            patch.object(diagnostic, 'emit', side_effect=lambda value, _: self.events.append(value)),
        ]
        for replacement in patches:
            replacement.start()
            self.addCleanup(replacement.stop)

    def sleep(self, delay):
        self.assertLessEqual(delay, 5)
        self.clock[0] += delay

    def test_actual_work_waits_for_grant_and_never_logs_token(self):
        self.acquire.side_effect = [
            {'ok': True, 'granted': False, 'status': 'waiting_background', 'lease_token': 'secret'},
            {'ok': True, 'granted': True, 'status': 'granted', 'lease_token': 'secret'},
        ]
        with diagnostic.research_lease(self.state, Path('.')):
            self.assertEqual(self.acquire.call_count, 2)
            self.assertEqual(self.clock[0], 5)
            self.release.assert_not_called()
        self.release.assert_called_once_with()
        self.acquire.assert_called_with('job-fixture', 'cycle-fixture')
        self.assertNotIn('secret', str(self.events))
        self.assertNotIn('lease_token', str(self.events))
        self.assertTrue(self.events[-1]['released'])

    def test_wait_timeout_cancels_pending_lease_without_any_work(self):
        self.acquire.return_value = {'ok': True, 'granted': False, 'status': 'busy'}
        work = Mock()
        with self.assertRaisesRegex(RuntimeError, 'lease_wait_timeout'):
            with diagnostic.research_lease(self.state, Path('.'), timeout=120):
                work()
        work.assert_not_called()
        self.assertEqual(self.clock[0], 120)
        self.release.assert_called_once_with()

    def test_release_on_exception_keyboard_interrupt_and_system_exit(self):
        self.acquire.return_value = {'ok': True, 'granted': True, 'status': 'granted'}
        for exception in (ValueError, KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception.__name__):
                self.release.reset_mock()
                with self.assertRaises(exception):
                    with diagnostic.research_lease(self.state, Path('.')):
                        raise exception()
                self.release.assert_called_once_with()

    def test_interrupt_while_acquiring_still_cancels_pending_lease(self):
        self.acquire.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            with diagnostic.research_lease(self.state, Path('.')):
                self.fail('Work must not start')
        self.release.assert_called_once_with()

    def test_release_failure_has_bounded_retries(self):
        self.acquire.return_value = {'ok': True, 'granted': True}
        self.release.side_effect = [OSError('not emitted'), False, True]
        with diagnostic.research_lease(self.state, Path('.')):
            pass
        self.assertEqual(self.release.call_count, 3)
        self.assertEqual(self.clock[0], 4)
        self.assertTrue(self.events[-1]['released'])
        self.assertNotIn('not emitted', str(self.events))


if __name__ == '__main__':
    unittest.main()
