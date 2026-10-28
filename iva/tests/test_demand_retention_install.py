"""Unit installation is tested without systemd, root writes, or live databases."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import install_demand_retention_timer as installer


class RetentionTimerState(unittest.TestCase):
    def test_missing_unit_is_confirmed_through_systemd_load_state(self):
        with patch.object(installer.subprocess, 'run', side_effect=[
            subprocess.CompletedProcess([], 1, '', 'missing unit'),
            subprocess.CompletedProcess([], 0, 'not-found\n', '')]):
            self.assertEqual(installer.state('is-enabled', installer.TIMER), 'not-found')

    def test_systemd_failure_is_not_treated_as_absent_unit(self):
        with patch.object(installer.subprocess, 'run', return_value=
                          subprocess.CompletedProcess([], 1, '', 'bus unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot read unit state'):
                installer.state('is-enabled', installer.TIMER)


class RetentionTimerInstallation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.units = self.root / 'systemd'
        self.units.mkdir()
        self.release = self.root / 'data/demand-release-test'
        self.release.mkdir(parents=True)
        hashes = {}
        for rel in installer.REQUIRED_CODE:
            source = self.root / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('reviewed fixture: ' + rel)
            staged = self.release / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            hashes[rel] = installer.digest(source)
        (self.release / 'installation.json').write_text(json.dumps({'installed': True, 'sha256': hashes}))
        self.patch(patch.object(installer, 'ROOT', self.root))
        self.patch(patch.object(installer, 'UNIT_ROOT', self.units))
        self.status = {('is-enabled', installer.TIMER): 'not-found',
                       ('is-active', installer.TIMER): 'inactive',
                       ('is-active', installer.SERVICE): 'inactive'}
        self.patch(patch.object(installer, 'state', side_effect=lambda verb, unit: self.status[(verb, unit)]))
        self.commands = self.patch(patch.object(installer, 'run'))
        self.replacements = []

        def replace(source, target, uid, gid, mode):
            self.replacements.append((target.name, uid, gid, mode))
            shutil.copy2(source, target)
            target.chmod(mode)

        self.patch(patch.object(installer, 'replace', side_effect=replace))

    def patch(self, manager):
        result = manager.start()
        self.addCleanup(manager.stop)
        return result

    def test_success_verifies_root_units_then_enables_without_starting(self):
        report = installer.install_units(self.release)
        commands = [item.args[0] for item in self.commands.call_args_list]
        self.assertEqual(commands, [
            ['systemd-analyze', 'verify', *[str(self.units / name) for name in installer.UNITS]],
            ['systemctl', 'daemon-reload'], ['systemctl', 'enable', installer.TIMER]])
        self.assertEqual(self.replacements, [(name, 0, 0, 0o644) for name in installer.UNITS])
        self.assertTrue(report['timer_enabled'])
        self.assertFalse(report['timer_started'])
        self.assertFalse(report['service_started'])
        for name in installer.UNITS:
            self.assertEqual((self.units / name).read_bytes(), (self.root / 'deploy/systemd' / name).read_bytes())

    def test_modified_or_unsuccessful_code_is_rejected_before_systemd(self):
        (self.root / installer.REQUIRED_CODE[0]).write_text('unreviewed mutation')
        with self.assertRaisesRegex(RuntimeError, 'differs from the successful release'):
            installer.install_units(self.release)
        self.commands.assert_not_called()
        self.assertEqual(list(self.units.iterdir()), [])
        (self.release / 'installation.json').write_text('{"installed": false}')
        with self.assertRaisesRegex(RuntimeError, 'successful code deployment is required'):
            installer.install_units(self.release)

    def test_active_service_is_not_interrupted(self):
        self.status[('is-active', installer.SERVICE)] = 'active'
        with self.assertRaisesRegex(RuntimeError, 'service is active'):
            installer.install_units(self.release)
        self.commands.assert_not_called()
        self.assertEqual(list(self.units.iterdir()), [])

    def test_verification_failure_removes_only_new_units(self):
        unrelated = self.units / 'other.service'
        unrelated.write_text('unchanged')

        def fail_verify(args):
            if args[0] == 'systemd-analyze':
                raise subprocess.CalledProcessError(1, args)

        self.commands.side_effect = fail_verify
        with self.assertRaises(subprocess.CalledProcessError):
            installer.install_units(self.release)
        self.assertEqual(list(self.units.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_text(), 'unchanged')
        commands = [item.args[0] for item in self.commands.call_args_list]
        self.assertIn(['systemctl', 'disable', '--now', installer.TIMER], commands)
        self.assertFalse(any('start' in args for args in commands))
        self.assertFalse((self.release / 'retention-units-installation.json').exists())

    def test_failed_update_restores_existing_units_and_active_timer(self):
        for name in installer.UNITS:
            (self.units / name).write_text('old ' + name)
        self.status[('is-enabled', installer.TIMER)] = 'enabled'
        self.status[('is-active', installer.TIMER)] = 'active'

        def fail_verify(args):
            if args[0] == 'systemd-analyze':
                raise subprocess.CalledProcessError(1, args)

        self.commands.side_effect = fail_verify
        # Server tests run as iva. Model root ownership for these isolated
        # fixture units without chowning any real /etc files.
        original_stat = Path.stat

        def root_owned_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path.parent == self.units:
                fields = list(info); fields[4] = 0; fields[5] = 0
                return os.stat_result(fields)
            return info

        with patch.object(Path, 'stat', root_owned_stat), self.assertRaises(subprocess.CalledProcessError):
            installer.install_units(self.release)
        for name in installer.UNITS:
            self.assertEqual((self.units / name).read_text(), 'old ' + name)
        commands = [item.args[0] for item in self.commands.call_args_list]
        self.assertEqual(commands[0], ['systemctl', 'stop', installer.TIMER])
        self.assertEqual(commands[-2:], [['systemctl', 'enable', installer.TIMER],
                                        ['systemctl', 'start', installer.TIMER]])
        self.assertNotIn(['systemctl', 'start', installer.SERVICE], commands)


if __name__ == '__main__':
    unittest.main()
