"""Narrow reader deployment with isolated files, SQLite and fake systemd."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import install_demand_content_quality as installer


class Installation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = self.root / 'data/release'
        self.stage = self.release / 'source'
        self.stage.mkdir(parents=True)
        self.mock(patch.object(installer, 'ROOT', self.root))
        self.mock(patch.object(installer, 'RELEASE', self.release))
        self.old = {}
        for name in installer.FILES:
            path = self.stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('new ' + name)
            if name in ('iva_integration/demand_sources.py', 'iva_integration/demand_agents.py', 'deploy/run_supervised_demand.py'):
                target = self.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('old ' + name)
                self.old[name] = target.read_bytes()
        (self.release / 'source-sha256.json').write_text(json.dumps({
            name: installer.digest(self.stage / name) for name in installer.FILES}))
        (self.release / 'expected-base.json').write_text(json.dumps({
            str(self.root / name): installer.digest(self.root / name) for name in installer.FILES}))
        self.state = 'active'
        self.commands = []
        self.mock(patch.object(installer, 'active', side_effect=lambda: self.state))
        self.idle = self.mock(patch.object(installer, 'require_idle'))
        self.preflight = self.mock(patch.object(installer, 'preflight'))
        self.mock(patch.object(installer, 'protected', return_value={'operations': 'unchanged'}))

        def run(args, **kwargs):
            self.commands.append(args)
            if 'stop' in args:
                self.state = 'inactive'
            if 'start' in args:
                self.state = 'active'
            return subprocess.CompletedProcess(args, 0, 'enabled\n' if 'show' in args else '', '')

        self.mock(patch.object(installer, 'run', side_effect=run))

        def pause(mark):
            mark()
            installer.run(['systemctl', '--no-block', 'stop', installer.SCHEDULER])

        self.pause = self.mock(patch.object(installer, 'pause_scheduler', side_effect=pause))

        def replace(source, target, *attrs):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

        self.replace = self.mock(patch.object(installer, 'replace', side_effect=replace))
        self.account = SimpleNamespace(pw_uid=1000, pw_gid=1000)

    def mock(self, manager):
        value = manager.start()
        self.addCleanup(manager.stop)
        return value

    def test_installs_reviewed_files_and_only_pauses_scheduler(self):
        report = installer.install(self.account)
        self.assertTrue(report['installed'])
        self.assertEqual(self.state, 'active')
        self.assertEqual([args for args in self.commands if 'stop' in args or 'start' in args], [
            ['systemctl', '--no-block', 'stop', installer.SCHEDULER],
            ['systemctl', 'start', installer.SCHEDULER]])
        for name in installer.FILES:
            self.assertEqual((self.root / name).read_bytes(), (self.stage / name).read_bytes())
        self.assertEqual(self.replace.call_args_list[0].args[1], self.root / 'iva_integration/demand_content_quality.py')

    def test_inactive_scheduler_remains_inactive(self):
        self.state = 'inactive'
        installer.install(self.account)
        self.assertEqual(self.state, 'inactive')
        self.pause.assert_not_called()
        self.assertFalse(any('start' in args or 'stop' in args for args in self.commands))

    def test_failed_tests_and_concurrent_source_changes_prevent_service_actions(self):
        self.preflight.side_effect = RuntimeError('isolated test failed')
        with self.assertRaises(RuntimeError):
            installer.install(self.account)
        self.assertEqual(self.commands, [])
        self.replace.assert_not_called()
        self.preflight.side_effect = lambda: (self.stage / installer.FILES[0]).write_text('unreviewed')
        with self.assertRaisesRegex(RuntimeError, 'changed since review'):
            installer.install(self.account)
        self.assertEqual(self.commands, [])

    def test_replace_failure_restores_preexisting_files_and_activity(self):
        normal = self.replace.side_effect

        def fail_once(source, target, *attrs):
            if source == self.stage / 'iva_integration/demand_sources.py':
                raise OSError('isolated replace failure')
            normal(source, target, *attrs)

        self.replace.side_effect = fail_once
        with self.assertRaises(OSError):
            installer.install(self.account)
        self.assertEqual(self.state, 'active')
        for name in installer.FILES:
            if name in self.old:
                self.assertEqual((self.root / name).read_bytes(), self.old[name])
            else:
                self.assertFalse((self.root / name).exists())
        self.assertFalse((self.release / 'installation.json').exists())

    def test_busy_work_prevents_preflight_and_stop(self):
        self.idle.side_effect = RuntimeError('busy')
        with self.assertRaises(RuntimeError):
            installer.install(self.account)
        self.preflight.assert_not_called()
        self.assertEqual(self.commands, [])

    def test_incomplete_file_rollback_does_not_start_mixed_sources(self):
        normal = self.replace.side_effect

        def fail(source, target, *attrs):
            if source == self.stage / 'iva_integration/demand_sources.py':
                raise OSError('install failed')
            if source.parent == self.release / 'before':
                raise OSError('restore failed')
            normal(source, target, *attrs)

        self.replace.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, 'rollback needs review'):
            installer.install(self.account)
        self.assertEqual(self.state, 'inactive')
        self.assertNotIn(['systemctl', 'start', installer.SCHEDULER], self.commands)

    def test_admission_race_can_retry_without_replacing_first_backup(self):
        original_pause = self.pause.side_effect
        self.pause.side_effect = RuntimeError('work became busy before stop')
        with self.assertRaisesRegex(RuntimeError, 'became busy'):
            installer.install(self.account)
        first = self.release / 'before'
        original_files = {path.name: path.read_bytes() for path in first.iterdir()}
        self.assertTrue(original_files)
        self.assertEqual(self.state, 'active')
        self.replace.assert_not_called()
        self.assertFalse(any('stop' in args or 'start' in args for args in self.commands))
        self.pause.side_effect = original_pause
        report = installer.install(self.account)
        self.assertEqual(report['rollback'], str(self.release / 'before-2'))
        self.assertTrue((self.release / 'before-2/state.json').exists())
        self.assertEqual(original_files, {path.name: path.read_bytes() for path in first.iterdir()})

    def test_retry_still_rejects_runtime_source_changes_before_new_backup(self):
        self.pause.side_effect = RuntimeError('work became busy')
        with self.assertRaises(RuntimeError):
            installer.install(self.account)
        (self.root / 'iva_integration/demand_sources.py').write_text('external edit after abort')
        with self.assertRaisesRegex(RuntimeError, 'Runtime source changed'):
            installer.install(self.account)
        self.assertFalse((self.release / 'before-2').exists())


class Admission(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        self.path = self.root / 'data/local-finance.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            for table in ('local_updates', 'scheduled_jobs', 'scheduled_runs', 'scheduled_outbox'):
                db.execute('CREATE TABLE ' + table + '(state TEXT)')
            db.commit()

    def test_every_active_transition_blocks_stop(self):
        with patch.object(installer, 'coordinator_idle', return_value=True), \
             patch.object(installer, 'standalone_finished', return_value=True):
            for table, state in (('local_updates', 'processing'), ('scheduled_jobs', 'running'),
                                 ('scheduled_runs', 'running'), ('scheduled_outbox', 'sending')):
                with closing(sqlite3.connect(self.path)) as db:
                    db.execute('INSERT INTO ' + table + ' VALUES(?)', (state,))
                    self.assertFalse(installer.idle(db), table)
                    db.rollback()

    def test_stop_blocks_claim_send_writers_and_releases_without_data_changes(self):
        before = self.path.read_bytes()
        states = iter(['deactivating', 'inactive'])

        def stopped(args, **kwargs):
            self.assertEqual(args, ['systemctl', '--no-block', 'stop', installer.SCHEDULER])
            with closing(sqlite3.connect(self.path, timeout=0)) as competing:
                with self.assertRaises(sqlite3.OperationalError):
                    competing.execute('BEGIN IMMEDIATE')

        marks = []
        with patch.object(installer, 'ROOT', self.root), patch.object(installer, 'coordinator_idle', return_value=True), \
             patch.object(installer, 'standalone_finished', return_value=True), \
             patch.object(installer, 'run', side_effect=stopped), \
             patch.object(installer, 'active', side_effect=lambda: next(states)), \
             patch.object(installer.time, 'sleep'):
            installer.pause_scheduler(lambda: marks.append('requested'))
        self.assertEqual(marks, ['requested'])
        self.assertEqual(self.path.read_bytes(), before)
        with closing(sqlite3.connect(self.path, timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE')
            db.rollback()

    def test_active_standalone_or_research_cannot_be_interrupted(self):
        with closing(sqlite3.connect(self.path)) as db:
            for coordinator, standalone in ((False, True), (True, False)):
                with patch.object(installer, 'coordinator_idle', return_value=coordinator), \
                     patch.object(installer, 'standalone_finished', return_value=standalone):
                    self.assertFalse(installer.idle(db))

    def test_r2_requires_second_attempt_terminal_proof_and_no_active_units(self):
        first = self.root / 'data/demand-supervised-20261025'
        first.mkdir()
        (first / 'state.json').write_text(json.dumps({'version': 1, 'state': 'completed'}))
        second = self.root / 'data/demand-supervised-20261025-attempt2'
        second.mkdir()
        with patch.object(installer, 'ROOT', self.root), patch.object(installer, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '', '')
            self.assertFalse(installer.standalone_finished())
            for state in ('running', 'suspended', 'waiting'):
                (second / 'state.json').write_text(json.dumps({'version': 1, 'attempt': 2, 'state': state}))
                self.assertFalse(installer.standalone_finished())
            (second / 'state.json').write_text(json.dumps({'version': 1, 'attempt': 1, 'state': 'completed'}))
            self.assertFalse(installer.standalone_finished())
            (second / 'state.json').write_text(json.dumps({'version': 1, 'attempt': 2, 'state': 'completed_with_gaps'}))
            self.assertTrue(installer.standalone_finished())
            run.return_value = subprocess.CompletedProcess([], 0, 'iva-demand-supervised-attempt2.service active running', '')
            self.assertFalse(installer.standalone_finished())


if __name__ == '__main__':
    unittest.main()
