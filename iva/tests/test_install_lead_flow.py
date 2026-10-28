"""Isolated service doubles: no real systemd, sudo, models or financial writes."""
from contextlib import closing, nullcontext
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load_installer():
    path = Path(__file__).resolve().parents[1]/'deploy/install_lead_flow.py'
    spec = importlib.util.spec_from_file_location('lead_installer_isolated_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'fcntl':SimpleNamespace(), 'pwd':SimpleNamespace()}):
        spec.loader.exec_module(module)
    return module


class AdmissionTests(unittest.TestCase):
    def test_writer_barrier_released_before_wait_and_no_committed_changes(self):
        module = load_installer()
        with tempfile.TemporaryDirectory() as folder:
            module.ROOT = Path(folder)
            path = module.ROOT/'data/local-finance.sqlite3'
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE scheduled_outbox(state)')
                db.execute("INSERT INTO scheduled_outbox VALUES('pending')")
                db.commit()
            commands = []
            def command(args, **kwargs):
                commands.append(args)
                with closing(sqlite3.connect(path, timeout=0)) as probe:
                    with self.assertRaises(sqlite3.OperationalError):
                        probe.execute('BEGIN IMMEDIATE')
            def waiting(unit):
                with closing(sqlite3.connect(path, timeout=0)) as probe:
                    probe.execute('BEGIN IMMEDIATE')
                    self.assertEqual(probe.execute('SELECT state FROM scheduled_outbox').fetchall(), [('pending',)])
                    probe.rollback()
            helper = SimpleNamespace(require_quiet=lambda:None)
            with patch.dict(sys.modules, {'deploy.install_recovery_notices':helper}), patch.object(module,'idle_model_locks',return_value=nullcontext()), patch.object(module,'run',side_effect=command), patch.object(module,'wait_inactive',side_effect=waiting):
                lifecycle = {}
                module.stop_idle_engine(True,lifecycle)
            self.assertEqual(commands,[['systemctl','stop','iva@scheduler.service'],['systemctl','--no-block','stop','iva@engine.service']])
            self.assertTrue(lifecycle['engine_stop_requested'])

    def test_busy_owner_refuses_before_stop_and_inflight_delivery_also_refuses(self):
        module = load_installer()
        with tempfile.TemporaryDirectory() as folder:
            module.ROOT = Path(folder)
            path = module.ROOT/'data/local-finance.sqlite3'
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE scheduled_outbox(state)')
                db.execute("INSERT INTO scheduled_outbox VALUES('sending')")
                db.commit()
            for quiet in (lambda:(_ for _ in ()).throw(RuntimeError('busy')), lambda:None):
                helper = SimpleNamespace(require_quiet=quiet)
                with patch.dict(sys.modules, {'deploy.install_recovery_notices':helper}), patch.object(module,'idle_model_locks',return_value=nullcontext()), patch.object(module,'run') as command:
                    lifecycle = {}
                    with self.assertRaises(RuntimeError):
                        module.stop_idle_engine(True,lifecycle)
                    command.assert_not_called()
                    self.assertEqual(lifecycle,{})


class ControlRolloutTests(unittest.TestCase):
    def setUp(self):
        self.module = load_installer()
        self.tmp = tempfile.TemporaryDirectory()
        self.module.ROOT = Path(self.tmp.name)/'iva'
        self.module.RELEASE = Path(self.tmp.name)/'release'
        self.module.RELEASE.mkdir()
        self.app = self.module.ROOT/'data/iva-pilot/app'
        (self.app/'.output').mkdir(parents=True)
        (self.app/'.output/old').write_text('original compiled code')
        (self.app/'.eve').mkdir()
        (self.app/'.eve/agent-summary.json').write_text(json.dumps({'tools':[{'name':'existing'}]}))
        self.target = self.module.ROOT/'iva_integration/lead_research.py'
        self.target.parent.mkdir()
        self.target.write_text('original source')
        (self.module.RELEASE/'source').mkdir()
        (self.module.RELEASE/'source/one.py').write_text('new source')
        self.manifest = [{'target':str(self.target),'source':'one.py'}]
        self.events = []
        self.helpers = SimpleNamespace(
            require_quiet=lambda:self.events.append('quiet'),
            protected_snapshot=lambda:{'finance':'same'},
            unit_state=lambda *args:'active',
            pause_telegram=lambda:self.events.append('telegram_pause'),
            restore_telegram=lambda:self.events.append('telegram_restore'),
            restart_gateway=lambda:self.events.append('gateway_restart'))

    def tearDown(self):
        self.tmp.cleanup()

    def fake_put(self,source,target,owner,mode):
        if target.exists():
            before = self.module.backup_path(target)
            if not before.exists():
                before.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(target,before)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)

    def fake_stop(self,scheduler,lifecycle):
        self.events.append('engine_stop_admission')
        lifecycle.update(engine_stop_requested=True,scheduler_stop_requested=scheduler)

    def fake_run(self,args,**kwargs):
        self.events.append(' '.join(str(arg) for arg in args))
        if '-c' in args:
            self.events.append('build')
            (self.app/'.output').mkdir()
            (self.app/'.output/new').write_text('new compiled code')
            (self.app/'.eve/agent-summary.json').write_text(json.dumps({'tools':[{'name':'existing'},{'name':'lead_research'}]}))
        return SimpleNamespace(stdout='inactive')

    def patches(self):
        return patch.dict(sys.modules,{'deploy.install_recovery_notices':self.helpers})

    def test_pause_then_build_then_gateway_engine_and_restore(self):
        with self.patches(), patch.object(self.module,'put',side_effect=self.fake_put), patch.object(self.module,'stop_idle_engine',side_effect=self.fake_stop), patch.object(self.module,'run',side_effect=self.fake_run), patch.object(self.module,'verify_native_ready',side_effect=lambda:self.events.append('verified')):
            result = self.module.install_control(self.manifest,(0,0))
        self.assertTrue(result['native_tool_verified'])
        self.assertLess(self.events.index('telegram_pause'),self.events.index('engine_stop_admission'))
        self.assertLess(self.events.index('build'),self.events.index('gateway_restart'))
        self.assertLess(self.events.index('verified'),self.events.index('telegram_restore'))
        self.assertTrue((self.module.RELEASE/'before-control-output/old').exists())
        self.assertEqual(self.target.read_text(),'new source')

    def test_failed_build_restores_source_bundle_and_activity(self):
        def failed(args,**kwargs):
            result = self.fake_run(args,**kwargs)
            if '-c' in args:
                raise subprocess.CalledProcessError(1,args)
            return result
        with self.patches(), patch.object(self.module,'put',side_effect=self.fake_put), patch.object(self.module,'stop_idle_engine',side_effect=self.fake_stop), patch.object(self.module,'run',side_effect=failed), patch.object(self.module,'wait_inactive'):
            with self.assertRaises(subprocess.CalledProcessError):
                self.module.install_control(self.manifest,(0,0))
        self.assertEqual(self.target.read_text(),'original source')
        self.assertTrue((self.app/'.output/old').exists())
        self.assertTrue((self.module.RELEASE/'failed-control-output/new').exists())
        self.assertNotIn('gateway_restart',self.events)
        self.assertIn('systemctl start iva@engine.service',self.events)
        self.assertIn('telegram_restore',self.events)

    def test_busy_admission_never_stops_engine_or_modifies_source(self):
        def busy(scheduler,lifecycle):
            raise RuntimeError('owner_became_busy')
        with self.patches(), patch.object(self.module,'stop_idle_engine',side_effect=busy), patch.object(self.module,'run',side_effect=self.fake_run), patch.object(self.module,'put',side_effect=self.fake_put):
            with self.assertRaises(RuntimeError):
                self.module.install_control(self.manifest,(0,0))
        self.assertFalse(any('iva@engine' in value for value in self.events))
        self.assertEqual(self.target.read_text(),'original source')
        self.assertTrue((self.app/'.output/old').exists())


if __name__ == '__main__':
    unittest.main()
