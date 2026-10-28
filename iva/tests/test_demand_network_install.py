"""Research reader rollback tests: temporary files and mocked system commands."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'deploy/install_research_fetch.py'
SPEC = importlib.util.spec_from_file_location('research_fetch_install_test', SOURCE)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
HOST_ROUTE = 'default via host-test dev eth0\n'
VPN_ROUTE = 'default dev awg-iva\n'


class ReaderRollback(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / 'data/demand-release-test'
        self.release.mkdir(parents=True)
        self.helper = self.root / 'installed/server.py'
        self.unit = self.root / 'installed/iva-research-fetch.service'
        self.helper.parent.mkdir()
        self.helper.write_text('old helper')
        self.unit.write_text('old unit')
        for name, value in [('ROOT', self.root), ('HELPER', self.helper), ('UNIT', self.unit)]:
            patched = patch.object(installer, name, value)
            patched.start(); self.addCleanup(patched.stop)

    def command(self, args):
        if args[:2] == ['ip', 'route']:
            return SimpleNamespace(stdout=HOST_ROUTE)
        if args[:3] == ['ip', '-n', 'iva-codex']:
            return SimpleNamespace(stdout=VPN_ROUTE)
        return SimpleNamespace(stdout='active\n')

    def rollback(self, existing=None, old_active=True, old_enabled=True, replace=None):
        return installer.rollback_reader(existing or {}, old_active, old_enabled,
            self.release, HOST_ROUTE, VPN_ROUTE, replace or Mock())

    def test_stop_and_disable_failures_do_not_skip_other_restoration(self):
        def commands(args):
            if args[:2] in (['systemctl', 'stop'], ['systemctl', 'disable']):
                raise subprocess.CalledProcessError(1, args, output='SECRET OUTPUT', stderr='PRIVATE')
            return self.command(args)
        replace = Mock()
        with patch.object(installer, 'run', side_effect=commands) as run:
            report = self.rollback({self.helper: self.root/'backup-helper', self.unit: self.root/'backup-unit'}, replace=replace)
        self.assertEqual(replace.call_count, 2)
        self.assertEqual(report['failed_steps'], ['stop_reader', 'disable_reader'])
        self.assertIn(['systemctl', 'daemon-reload'], [call.args[0] for call in run.call_args_list])
        self.assertIn(['systemctl', 'start', installer.NAME], [call.args[0] for call in run.call_args_list])
        saved = (self.release/'network-rollback.json').read_text()
        self.assertEqual(json.loads(saved), report)
        for private in ('SECRET', 'PRIVATE', HOST_ROUTE, VPN_ROUTE):
            self.assertNotIn(private, saved)
        self.assertTrue(report['host_route_unchanged']); self.assertTrue(report['vpn_route_unchanged'])

    def test_helper_restore_failure_still_restores_unit_but_never_starts_mixed_code(self):
        replace = Mock(side_effect=[OSError('SECRET'), None])
        with patch.object(installer, 'run', side_effect=self.command) as run:
            report = self.rollback({self.helper: self.root/'backup-helper', self.unit: self.root/'backup-unit'}, replace=replace)
        self.assertEqual(replace.call_count, 2)
        self.assertEqual(replace.call_args_list[1].args[1], self.unit)
        self.assertIn('restore_helper', report['failed_steps'])
        self.assertIn('start_previous_reader_skipped_incomplete_restore', report['failed_steps'])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(['systemctl', 'daemon-reload'], commands)
        self.assertNotIn(['systemctl', 'enable', installer.NAME], commands)
        self.assertNotIn(['systemctl', 'start', installer.NAME], commands)

    def test_new_install_is_removed_without_reenabling_or_starting(self):
        with patch.object(installer, 'run', side_effect=self.command) as run:
            report = self.rollback(old_active=False, old_enabled=False)
        self.assertTrue(report['rollback_complete'])
        self.assertFalse(self.helper.exists()); self.assertFalse(self.unit.exists())
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([item for item in commands if item[0] == 'systemctl'], [
            ['systemctl', 'stop', installer.NAME], ['systemctl', 'disable', installer.NAME],
            ['systemctl', 'daemon-reload']])

    def test_failed_daemon_reload_prevents_start_and_still_checks_routes(self):
        def commands(args):
            if args == ['systemctl', 'daemon-reload']:
                raise OSError('private unit output')
            return self.command(args)
        with patch.object(installer, 'run', side_effect=commands) as run:
            report = self.rollback({self.helper: self.root/'old-helper', self.unit: self.root/'old-unit'})
        self.assertIn('reload_units', report['failed_steps'])
        self.assertIn('start_previous_reader_skipped_incomplete_restore', report['failed_steps'])
        self.assertTrue(report['host_route_unchanged']); self.assertTrue(report['vpn_route_unchanged'])
        self.assertNotIn(['systemctl', 'start', installer.NAME], [call.args[0] for call in run.call_args_list])

    def test_changed_or_unavailable_routes_are_reported_and_never_modified(self):
        def commands(args):
            if args[:2] == ['ip', 'route']:
                return SimpleNamespace(stdout='different private route')
            if args[:2] == ['ip', '-n']:
                raise OSError('private namespace details')
            return self.command(args)
        with patch.object(installer, 'run', side_effect=commands) as run:
            report = self.rollback(old_active=False, old_enabled=False)
        self.assertFalse(report['host_route_unchanged']); self.assertIsNone(report['vpn_route_unchanged'])
        self.assertEqual(report['failed_steps'], ['host_route_changed', 'vpn_route_check'])
        self.assertFalse(report['routes_modified']); self.assertFalse(report['vpn_restarted'])
        self.assertTrue(all(call.args[0] in (
            ['systemctl', 'stop', installer.NAME], ['systemctl', 'disable', installer.NAME],
            ['systemctl', 'daemon-reload'], ['ip', 'route', 'show', 'default'],
            ['ip', '-n', 'iva-codex', 'route', 'show', 'default']) for call in run.call_args_list))
        self.assertNotIn('private', (self.release/'network-rollback.json').read_text())

    def test_report_write_failure_does_not_raise(self):
        with patch.object(installer, 'run', side_effect=self.command), \
                patch.object(Path, 'write_text', side_effect=OSError('SECRET')):
            report = self.rollback(old_active=False, old_enabled=False)
        self.assertFalse(report['rollback_complete'])
        self.assertEqual(report['failed_steps'], ['write_rollback_report'])

    def test_main_reraises_original_install_error_despite_cleanup_failure(self):
        required = ('deploy/research_fetch_service.py', 'deploy/systemd/' + installer.NAME,
                    'iva_integration/demand_network.py', 'iva_integration/demand_sources.py')
        for relative in required:
            path = self.root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('test source')
        (self.release/'installation.json').write_text(json.dumps({'installed': True,
            'sha256': {relative: installer.sha(self.root/relative) for relative in required}}))
        (self.release/'expected-network.json').write_text(json.dumps({
            str(path): installer.sha(path) for path in (self.helper, self.unit)}))
        original = RuntimeError('original install failure')
        fake_upgrade = SimpleNamespace(idle=Mock(return_value=True), replace_source=Mock(side_effect=original))
        fake_fcntl = SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4)
        with patch.dict(sys.modules, {'fcntl': fake_fcntl, 'deploy.upgrade_demand_research': fake_upgrade}), \
                patch.object(installer.os, 'geteuid', return_value=0, create=True), \
                patch.object(Path, 'cwd', return_value=self.root), \
                patch.object(installer.sys, 'argv', ['installer', str(self.release)]), \
                patch.object(installer.sys, 'path', list(sys.path)), \
                patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(stdout='inactive\n')), \
                patch.object(installer, 'run', side_effect=self.command), \
                patch.object(installer, 'rollback_reader', side_effect=OSError('cleanup failed')) as rollback:
            with self.assertRaises(RuntimeError) as caught:
                installer.main()
        self.assertIs(caught.exception, original)
        rollback.assert_called_once()
        self.assertEqual(json.loads((self.release/'network-rollback.json').read_text())['failed_steps'], ['rollback_internal_error'])


if __name__ == '__main__':
    unittest.main()
