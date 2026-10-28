"""Backup failures must not consume the recovery copy or leave partial files."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import backup


class BackupCapacity(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = self.root / 'data'
        self.output = self.data / 'server-backups'
        self.output.mkdir(parents=True)
        self.temporary_root = self.root / 'tmp'
        self.temporary_root.mkdir()
        self.now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        self.target = self.output / 'iva-20261025-120000.tar.gz'
        self.partial = self.target.with_suffix('.partial')
        with contextlib.closing(sqlite3.connect(self.data / 'local-finance.sqlite3')) as db:
            db.execute('CREATE TABLE fixture(value TEXT)')
            db.execute("INSERT INTO fixture VALUES ('kept')")
            db.commit()
        self.start_patch(patch.object(backup, 'ROOT', self.root))
        self.start_patch(patch.object(backup.tempfile, 'gettempdir', return_value=str(self.temporary_root)))
        self.start_patch(patch.object(backup.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)))
        clock = self.start_patch(patch.object(backup, 'datetime', wraps=datetime))
        clock.now.return_value = self.now
        self.start_patch(contextlib.redirect_stdout(io.StringIO()))

    def start_patch(self, manager):
        if hasattr(manager, 'start'):
            result = manager.start()
            self.addCleanup(manager.stop)
        else:
            result = manager.__enter__()
            self.addCleanup(manager.__exit__, None, None, None)
        return result

    def old_archive(self):
        old = self.output / 'iva-20261001-120000.tar.gz'
        old.write_bytes(b'existing recovery copy')
        timestamp = (self.now - timedelta(days=24)).timestamp()
        os.utime(old, (timestamp, timestamp))
        return old

    def test_same_filesystem_peak_includes_db_snapshot_and_new_archive(self):
        databases = backup.database_inputs()
        plan = backup.space_plan(self.output, self.temporary_root, databases)
        self.assertTrue(plan['same_filesystem'])
        self.assertEqual(plan['output_required_bytes'], plan['archive_bound_bytes'] +
                         plan['snapshot_bytes'] + 512 * 1024**2)
        self.assertEqual(plan['temporary_required_bytes'], 0)

    def test_separate_temporary_filesystem_has_independent_reserve(self):
        fake_output = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=1))
        fake_temp = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=2))
        plan = backup.space_plan(fake_output, fake_temp, backup.database_inputs())
        self.assertFalse(plan['same_filesystem'])
        self.assertEqual(plan['output_required_bytes'], plan['archive_bound_bytes'] + backup.RESERVE_BYTES)
        self.assertEqual(plan['temporary_required_bytes'], plan['snapshot_bytes'] + backup.RESERVE_BYTES)
        with patch.object(backup, 'space_plan', return_value=plan), \
             patch.object(backup.shutil, 'disk_usage', side_effect=[
                 SimpleNamespace(free=plan['output_required_bytes']),
                 SimpleNamespace(free=plan['temporary_required_bytes'] - 1)]):
            with self.assertRaisesRegex(backup.BackupError, 'Insufficient temporary disk space'):
                backup.preflight(self.output, self.temporary_root, [])

    def test_wal_bytes_are_included_in_snapshot_headroom(self):
        database = self.data / 'demand-research.sqlite3'
        database.write_bytes(b'd' * 100)
        database.with_name(database.name + '-wal').write_bytes(b'w' * 70)
        estimate = {rel: size for rel, _, size in backup.database_inputs()}
        self.assertEqual(estimate['data/demand-research.sqlite3'], 170)

    def test_low_space_fails_before_snapshot_and_preserves_old_archives(self):
        old = self.old_archive()
        with patch.object(backup.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)), \
             patch.object(backup, 'copy_database') as copy:
            with self.assertRaisesRegex(backup.BackupError, 'existing archives preserved'):
                backup.main()
        copy.assert_not_called()
        self.assertEqual(old.read_bytes(), b'existing recovery copy')
        self.assertFalse(self.partial.exists())
        self.assertEqual(list(self.temporary_root.iterdir()), [])

    def test_archive_failure_cleans_own_partial_and_keeps_unrelated_files(self):
        old = self.old_archive()
        unrelated = self.output / 'iva-20261024-100000.tar.partial'
        unrelated.write_bytes(b'preexisting partial')
        with patch.object(backup.tarfile.TarFile, 'add', side_effect=OSError('simulated disk full')):
            with self.assertRaisesRegex(OSError, 'simulated disk full'):
                backup.main()
        self.assertEqual(old.read_bytes(), b'existing recovery copy')
        self.assertEqual(unrelated.read_bytes(), b'preexisting partial')
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.temporary_root.iterdir()), [])

    def test_snapshot_failure_removes_only_owned_temporary_directory(self):
        old = self.old_archive()
        unrelated = self.temporary_root / 'other-job'
        unrelated.mkdir()
        (unrelated / 'checkpoint').write_text('kept')
        with patch.object(backup, 'copy_database', side_effect=sqlite3.OperationalError('fixture error')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'fixture error'):
                backup.main()
        self.assertTrue(old.exists())
        self.assertEqual(list(self.temporary_root.iterdir()), [unrelated])
        self.assertEqual((unrelated / 'checkpoint').read_text(), 'kept')
        self.assertFalse(self.partial.exists())

    def test_existing_final_or_partial_is_never_overwritten(self):
        for collision in (self.target, self.partial):
            with self.subTest(path=collision.name):
                collision.write_bytes(b'other invocation')
                with self.assertRaisesRegex(backup.BackupError, 'timestamp already exists'):
                    backup.main()
                self.assertEqual(collision.read_bytes(), b'other invocation')
                collision.unlink()

    def test_publication_race_keeps_other_archive_and_cleans_own_partial(self):
        old = self.old_archive()

        def collide(source, destination):
            destination.write_bytes(b'other invocation won')
            raise FileExistsError('simulated timestamp race')

        with patch.object(backup.os, 'link', side_effect=collide):
            with self.assertRaisesRegex(FileExistsError, 'simulated timestamp race'):
                backup.main()
        self.assertEqual(self.target.read_bytes(), b'other invocation won')
        self.assertTrue(old.exists())
        self.assertFalse(self.partial.exists())
        self.assertEqual(list(self.temporary_root.iterdir()), [])

    def test_partial_cleanup_does_not_delete_file_with_different_identity(self):
        self.partial.write_bytes(b'foreign file')
        info = self.partial.stat()
        backup.remove_owned_partial(self.partial, (info.st_dev, info.st_ino + 1))
        self.assertEqual(self.partial.read_bytes(), b'foreign file')

    @unittest.skipUnless(os.name == 'posix', 'production uses POSIX flock')
    def test_concurrent_lock_refused_then_released(self):
        with backup.backup_lock(self.output):
            with self.assertRaisesRegex(backup.BackupError, 'Another backup is running'):
                with backup.backup_lock(self.output):
                    self.fail('second backup acquired the lock')
        with backup.backup_lock(self.output):
            pass

    def test_success_preserves_inclusion_set_and_then_applies_retention(self):
        old = self.old_archive()
        recent = self.output / 'iva-20261024-120000.tar.gz'
        recent.write_bytes(b'recent recovery copy')
        for rel, _, _ in backup.DATABASES[1:]:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE evidence(id INTEGER)')
                db.commit()
        for rel in backup.TREES:
            path = self.root / rel
            path.mkdir(parents=True, exist_ok=True)
            (path / 'fixture.txt').write_text('retained evidence')
        for rel in backup.FILES:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        backup.main()
        self.assertFalse(old.exists())
        self.assertEqual(recent.read_bytes(), b'recent recovery copy')
        self.assertFalse(self.partial.exists())
        self.assertEqual(list(self.temporary_root.iterdir()), [])
        with tarfile.open(self.target) as archive:
            names = set(archive.getnames())
            self.assertTrue({item[0] for item in backup.DATABASES}.issubset(names))
            self.assertTrue(set(backup.FILES).issubset(names))
            self.assertTrue({rel + '/fixture.txt' for rel in backup.TREES}.issubset(names))
            self.assertFalse(any('server-backups' in name for name in names))
            restored = self.root / 'restored.sqlite3'
            restored.write_bytes(archive.extractfile('data/local-finance.sqlite3').read())
        with contextlib.closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute('SELECT value FROM fixture').fetchall(), [('kept',)])


if __name__ == '__main__':
    unittest.main()
