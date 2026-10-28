"""Installer admission and source replacement, only in isolated directories."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'posix', 'VPS installer uses Unix locks and ownership')
class ResearchUpgrade(unittest.TestCase):
    def setUp(self):
        from deploy import upgrade_demand_research as upgrade
        self.upgrade = upgrade
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / 'isolated-schedule.sqlite3'
        with sqlite3.connect(self.database) as db:
            db.executescript('''CREATE TABLE local_updates(state TEXT);
                CREATE TABLE scheduled_jobs(id TEXT,state TEXT);
                CREATE TABLE scheduled_outbox(job_id TEXT,state TEXT);''')
        self.finance = patch.object(upgrade, 'finance', lambda: sqlite3.connect(self.database))
        self.finance.start(); self.addCleanup(self.finance.stop)
        self.priority = patch('iva_integration.research_priority.request',
                              return_value={'ok': True, 'status': 'idle', 'active': False})
        self.request = self.priority.start(); self.addCleanup(self.priority.stop)

    def test_only_idle_coordinator_allows_upgrade(self):
        self.assertTrue(self.upgrade.idle())
        for status in ('active', 'waiting_background', 'waiting_interactive', 'waiting_owner_lock'):
            with self.subTest(status=status):
                self.request.return_value = {'ok': True, 'status': status, 'active': False}
                self.assertFalse(self.upgrade.idle())
        self.request.return_value = {'ok': True, 'status': 'idle', 'active': True}
        self.assertFalse(self.upgrade.idle())

    def test_coordinator_error_fails_closed(self):
        self.request.side_effect = OSError('isolated test')
        self.assertFalse(self.upgrade.idle())
        self.request.side_effect = None
        self.request.return_value = {'ok': False, 'status': 'idle'}
        self.assertFalse(self.upgrade.idle())

    def test_work_or_pending_delivery_blocks_installation(self):
        statements = ["INSERT INTO local_updates VALUES('processing')",
                      "INSERT INTO scheduled_jobs VALUES('research','running')",
                      "INSERT INTO scheduled_jobs VALUES('research','active');"
                      "INSERT INTO scheduled_outbox VALUES('research','pending')"]
        for statement in statements:
            with self.subTest(statement=statement), sqlite3.connect(self.database) as db:
                db.executescript('DELETE FROM local_updates; DELETE FROM scheduled_jobs; DELETE FROM scheduled_outbox;')
                db.executescript(statement)
                self.assertFalse(self.upgrade.idle())

    def test_failed_replacement_preserves_old_source_and_cleans_temporary(self):
        source = self.root / 'new.py'; target = self.root / 'live.py'
        source.write_text('new source'); target.write_text('old source')
        with patch.object(self.upgrade.shutil, 'chown', side_effect=PermissionError('isolated test')):
            with self.assertRaises(PermissionError):
                self.upgrade.replace_source(source, target)
        self.assertEqual(target.read_text(), 'old source')
        self.assertEqual(list(self.root.glob('.*.deploy-*')), [])

    def test_replacement_is_complete_before_publication(self):
        source = self.root / 'new.py'; target = self.root / 'live.py'
        source.write_text('new source'); target.write_text('old source')
        replace = self.upgrade.os.replace
        def observe(temporary, destination):
            self.assertEqual(target.read_text(), 'old source')
            self.assertEqual(Path(temporary).read_text(), 'new source')
            replace(temporary, destination)
        with patch.object(self.upgrade.shutil, 'chown'), patch.object(self.upgrade.os, 'replace', side_effect=observe):
            self.upgrade.replace_source(source, target, mode=0o640)
        self.assertEqual(target.read_text(), 'new source')
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
