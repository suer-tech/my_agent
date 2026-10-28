"""Storage admission/maintenance safety with temporary DBs and inert leases."""
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from iva_integration import demand_storage as storage
from iva_integration import demand_schedule_retention as schedules
from iva_integration import demand_retention as retention
from iva_integration import demand_research, research_priority


class StorageRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.state = self.data / 'demand-storage'
        self.state.mkdir()
        self.ledger = self.data / 'local-finance.sqlite3'
        self.research = self.data / 'demand-research.sqlite3'
        for path in [self.ledger, self.research]:
            with closing(sqlite3.connect(path)) as db:
                db.executescript("CREATE TABLE sample(id INTEGER,value TEXT); INSERT INTO sample VALUES(1,'preserved');")
        self.stack = ExitStack()
        for key, value in [('ROOT', self.root), ('STATE', self.state), ('LEDGER', self.ledger), ('RESEARCH', self.research)]:
            self.stack.enter_context(patch.object(storage, key, value))
        self.disk = self.stack.enter_context(patch.object(storage.shutil, 'disk_usage',
                                                         return_value=SimpleNamespace(free=8 * 1024 ** 3)))

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    @contextmanager
    def runtime(self):
        events = []

        @contextmanager
        def inert_lock(path, create=False):
            events.append('lock:' + Path(path).name)
            yield
            events.append('unlock:' + Path(path).name)

        def compact(store, **kwargs):
            events.append('research-plan' if kwargs['dry_run'] else 'research-apply')
            return {'ok': True, 'dry_run': kwargs['dry_run'], 'cycles_compacted': 1,
                    'source_bodies_pruned': 0, 'more_eligible': False}

        def backup(*args):
            events.append('backup')
            return {'bytes': 100, 'generations': 1}

        def schedule_apply(*args, **kwargs):
            events.append('schedules-apply')
            return {'candidate_rows': 1, 'updated_rows': 1, 'estimated_savings_bytes': 100,
                    'more_eligible': False}

        fake_store = SimpleNamespace(close=Mock())
        with ExitStack() as stack:
            mocks = {
                'lock': stack.enter_context(patch.object(storage, 'lock', side_effect=inert_lock)),
                'busy': stack.enter_context(patch.object(storage, 'owner_or_schedule_busy', return_value=False)),
                'status': stack.enter_context(patch.object(research_priority, 'request', return_value={'status': 'idle'})),
                'acquire': stack.enter_context(patch.object(research_priority, 'acquire', return_value={'ok': True, 'granted': True})),
                'release': stack.enter_context(patch.object(research_priority, 'release', side_effect=lambda: (events.append('release'), True)[1])),
                'research_class': stack.enter_context(patch.object(demand_research, 'Research', return_value=fake_store)),
                'compact': stack.enter_context(patch.object(retention, 'compact', side_effect=compact)),
                'plan': stack.enter_context(patch.object(schedules, 'plan', return_value={
                    'protected_cycle_ids': ['current'], 'candidate_rows': 1, 'skipped': {},
                    'estimated_savings_bytes': 100, 'more_eligible': False})),
                'apply': stack.enter_context(patch.object(schedules, 'apply', side_effect=schedule_apply)),
                'backup': stack.enter_context(patch.object(storage, 'protected_backup', side_effect=backup)),
                'reclaim': stack.enter_context(patch.object(storage, 'reclaim_research_pages', return_value={'vacuum': False})),
                'events': events,
                'store': fake_store,
            }
            yield mocks

    def write_prior(self, **extra):
        value = {'last_compaction_at': datetime.now(timezone.utc).isoformat(),
                 'last_compaction': {'research': {'cycles_compacted': 1}}, **extra}
        storage.atomic_json(self.state / 'status.json', value)

    def test_admission_checks_only_new_cycles_and_keeps_existing_progress(self):
        self.disk.return_value.free = storage.MIN_NEW_CYCLE_FREE - 1
        self.assertFalse(storage.admission({})['allowed'])
        for cursor in [{'demand_cycle_id': 'running-cycle'}, {'demand_pending_result': {'text': 'not exported yet'}}]:
            self.disk.reset_mock()
            self.assertEqual(storage.admission(cursor), {'allowed': True, 'reason': 'existing_cycle'})
            self.disk.assert_not_called()
        self.disk.return_value.free = storage.MIN_NEW_CYCLE_FREE
        self.assertTrue(storage.admission({})['allowed'])
        self.disk.side_effect = OSError('fixture filesystem unavailable')
        self.assertEqual(storage.admission({}), {'allowed': False, 'reason': 'storage_unknown'})

    def test_busy_research_and_owner_do_not_request_or_release_existing_lease(self):
        with self.runtime() as mocks:
            mocks['status'].return_value = {'status': 'active', 'active': True}
            self.assertEqual(storage.maintain()['state'], 'waiting_for_idle')
            mocks['acquire'].assert_not_called()
            mocks['release'].assert_not_called()
            mocks['backup'].assert_not_called()
            mocks['apply'].assert_not_called()
        with self.runtime() as mocks:
            mocks['busy'].return_value = True
            self.assertEqual(storage.maintain()['state'], 'waiting_for_idle')
            mocks['acquire'].assert_not_called()
            mocks['apply'].assert_not_called()

    def test_waiting_browser_boundary_is_bounded_and_releases_own_request(self):
        with self.runtime() as mocks, patch.object(storage.time, 'monotonic', side_effect=[0, 26]), \
                patch.object(storage.time, 'sleep') as sleep:
            mocks['acquire'].return_value = {'ok': True, 'granted': False, 'status': 'waiting_interactive'}
            self.assertEqual(storage.maintain()['state'], 'waiting_for_browser_boundary')
            mocks['acquire'].assert_called_once()
            mocks['release'].assert_called_once()
            mocks['backup'].assert_not_called()
            mocks['apply'].assert_not_called()
            sleep.assert_not_called()

    def test_owner_appearing_after_lease_prevents_database_work(self):
        with self.runtime() as mocks:
            mocks['busy'].side_effect = [False, True]
            self.assertEqual(storage.maintain()['state'], 'waiting_for_idle')
            mocks['release'].assert_called_once()
            mocks['plan'].assert_not_called()
            mocks['backup'].assert_not_called()

    def test_backup_precedes_mutation_and_passes_current_cycle_protection(self):
        with self.runtime() as mocks:
            result = storage.maintain()
            self.assertEqual(result['state'], 'compacted')
            events = mocks['events']
            self.assertLess(events.index('backup'), events.index('research-apply'))
            self.assertLess(events.index('backup'), events.index('schedules-apply'))
            self.assertLess(events.index('lock:.backup.lock'), events.index('backup'))
            self.assertGreater(events.index('unlock:.backup.lock'), events.index('schedules-apply'))
            for call in mocks['compact'].call_args_list:
                self.assertEqual(call.kwargs['protected_cycle_ids'], ['current'])
            mocks['release'].assert_called_once()
            mocks['store'].close.assert_called_once()
            self.assertEqual(json.loads((self.state / 'status.json').read_text())['state'], 'compacted')

    def test_backup_failure_never_compacts_and_releases_lease(self):
        with self.runtime() as mocks:
            mocks['backup'].side_effect = RuntimeError('fixture insufficient backup capacity')
            with self.assertRaises(RuntimeError):
                storage.maintain()
            mocks['apply'].assert_not_called()
            self.assertTrue(all(call.kwargs['dry_run'] for call in mocks['compact'].call_args_list))
            mocks['release'].assert_called_once()
            mocks['store'].close.assert_called_once()

    def test_owner_appearing_during_backup_prevents_compaction(self):
        with self.runtime() as mocks:
            mocks['busy'].side_effect = [False, False, True]
            self.assertEqual(storage.maintain()['state'], 'waiting_for_idle')
            mocks['backup'].assert_called_once()
            self.assertTrue(all(call.kwargs['dry_run'] for call in mocks['compact'].call_args_list))
            mocks['apply'].assert_not_called()

    def test_schedule_only_old_material_also_triggers_backup_and_apply(self):
        with self.runtime() as mocks:
            mocks['compact'].side_effect = None
            mocks['compact'].return_value = {'cycles_compacted': 0, 'source_bodies_pruned': 0, 'more_eligible': False}
            self.assertEqual(storage.maintain()['state'], 'compacted')
            mocks['backup'].assert_called_once()
            mocks['apply'].assert_called_once()

    def test_malformed_scheduler_cursor_blocks_all_retention(self):
        with self.runtime() as mocks:
            mocks['plan'].return_value['skipped'] = {'job_cursor_invalid': 1}
            result = storage.maintain()
            self.assertNotEqual(result['state'], 'compacted')
            mocks['backup'].assert_not_called()
            mocks['apply'].assert_not_called()
            self.assertFalse(any(not call.kwargs['dry_run'] for call in mocks['compact'].call_args_list))

    def test_pending_continuation_survives_idle_deferral(self):
        self.write_prior(more_eligible=True)
        with self.runtime() as mocks:
            mocks['status'].return_value = {'status': 'active', 'active': True}
            report = storage.maintain()
            self.assertEqual(report['state'], 'waiting_for_idle')
            self.assertTrue(report['more_eligible'])
        with self.runtime() as mocks:
            self.assertEqual(storage.maintain()['state'], 'compacted')
            mocks['apply'].assert_called()

    def test_not_due_skips_all_runtime_arbitration(self):
        self.write_prior(more_eligible=False)
        with self.runtime() as mocks:
            self.assertEqual(storage.maintain()['state'], 'checked_not_due')
            mocks['status'].assert_not_called()
            mocks['acquire'].assert_not_called()
            mocks['backup'].assert_not_called()

    def test_schedule_batches_are_bounded_and_pending_is_recorded(self):
        with self.runtime() as mocks:
            mocks['apply'].side_effect = None
            mocks['apply'].return_value = {'updated_rows': 500, 'more_eligible': True}
            report = storage.maintain()
            self.assertEqual(mocks['apply'].call_count, 8)
            self.assertTrue(report['more_eligible'])
            self.assertEqual(len(report['last_compaction']['schedules']), 8)

    def test_committed_research_results_are_reported_if_scheduler_becomes_busy(self):
        with self.runtime() as mocks:
            mocks['busy'].side_effect = [False, False, False, True]
            report = storage.maintain()
            self.assertEqual(report['state'], 'continuation_pending')
            self.assertEqual(report['last_compaction']['research']['cycles_compacted'], 1)
            self.assertTrue(report['more_eligible'])
            mocks['apply'].assert_not_called()

    def test_lock_contention_defers_without_arbitration(self):
        with self.runtime() as mocks:
            @contextmanager
            def contention(path, create=False):
                if Path(path).name == 'application-deploy.lock':
                    raise BlockingIOError('fixture lock held')
                yield
            mocks['lock'].side_effect = contention
            self.assertEqual(storage.maintain()['state'], 'waiting_for_lock')
            mocks['acquire'].assert_not_called()
            mocks['backup'].assert_not_called()

    def test_daily_backup_lock_contention_defers_without_arbitration(self):
        with self.runtime() as mocks:
            @contextmanager
            def contention(path, create=False):
                if Path(path).name == '.backup.lock':
                    raise BlockingIOError('fixture backup running')
                yield
            mocks['lock'].side_effect = contention
            self.assertEqual(storage.maintain()['state'], 'waiting_for_lock')
            mocks['acquire'].assert_not_called()
            mocks['backup'].assert_not_called()
            mocks['apply'].assert_not_called()

    def test_failed_lease_release_is_persisted(self):
        with self.runtime() as mocks:
            mocks['release'].side_effect = None
            mocks['release'].return_value = False
            report = storage.maintain()
            self.assertTrue(report['release_pending'])
            self.assertTrue(json.loads((self.state / 'status.json').read_text())['release_pending'])

    def test_reaper_cleans_only_known_flat_temporary_files(self):
        partial = self.state / 'before-retention.tar.gz.partial'
        partial.write_bytes(b'owned interrupted archive')
        good = self.state / 'snapshot-known'
        good.mkdir()
        (good / 'research.sqlite3').write_bytes(b'temporary snapshot')
        (good / 'schedules.sqlite3-journal').write_bytes(b'temporary journal')
        unknown = self.state / 'snapshot-unrecognized'
        unknown.mkdir()
        (unknown / 'other.txt').write_text('keep')
        nested = self.state / 'snapshot-nested'
        nested.mkdir()
        (nested / 'child').mkdir()
        (nested / 'child' / 'research.sqlite3').write_bytes(b'keep')
        archive = self.state / 'before-retention.tar.gz'
        archive.write_bytes(b'preserved final generation')
        self.assertEqual(storage.reap_backup_temporary(self.state), 3)
        self.assertFalse(partial.exists())
        self.assertFalse(good.exists())
        self.assertEqual((unknown / 'other.txt').read_text(), 'keep')
        self.assertEqual((nested / 'child' / 'research.sqlite3').read_bytes(), b'keep')
        self.assertEqual(archive.read_bytes(), b'preserved final generation')

    def test_read_only_busy_probe_covers_owner_and_scheduler(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            db.executescript('CREATE TABLE local_updates(state TEXT); CREATE TABLE scheduled_jobs(state TEXT);')
            self.assertFalse(storage.owner_or_schedule_busy(self.ledger))
            db.execute("INSERT INTO local_updates VALUES('processing')")
            db.commit()
            self.assertTrue(storage.owner_or_schedule_busy(self.ledger))
            db.execute("UPDATE local_updates SET state='done'")
            db.execute("INSERT INTO scheduled_jobs VALUES('running')")
            db.commit()
            self.assertTrue(storage.owner_or_schedule_busy(self.ledger))
            db.execute("UPDATE scheduled_jobs SET state='active'")
            db.commit()
            self.assertFalse(storage.owner_or_schedule_busy(self.ledger))

    def test_small_database_is_not_vacuumed(self):
        before = self.research.read_bytes()
        result = storage.reclaim_research_pages(self.research)
        self.assertFalse(result['vacuum'])
        self.assertEqual(self.research.read_bytes(), before)

    def test_entrypoint_refuses_local_checkout(self):
        with self.assertRaisesRegex(RuntimeError, 'only_on_active_vps'):
            storage.main()

    @unittest.skipUnless(os.name == 'posix', 'POSIX lock semantics are verified on the VPS test host')
    def test_real_lock_prevents_second_maintenance_holder(self):
        path = self.state / 'real.lock'
        with storage.lock(path, create=True):
            with self.assertRaises(BlockingIOError):
                with storage.lock(path):
                    self.fail('second holder should not acquire lock')

    def test_backup_snapshots_committed_wal_and_keeps_sources_unchanged(self):
        with closing(sqlite3.connect(self.research)) as live:
            live.execute('PRAGMA journal_mode=WAL')
            live.execute("INSERT INTO sample VALUES(2,'committed in wal')")
            live.commit()
            originals = [path.read_bytes() for path in [self.research, self.ledger]]
            result = storage.protected_backup([self.research, self.ledger], self.state)
            self.assertEqual(result['generations'], 1)
            archive_path = self.state / 'before-retention.tar.gz'
            with tarfile.open(archive_path) as archive:
                self.assertEqual(set(archive.getnames()), {'research.sqlite3', 'schedules.sqlite3'})
                copied = self.root / 'restored.sqlite3'
                copied.write_bytes(archive.extractfile('research.sqlite3').read())
            with closing(sqlite3.connect(copied)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM sample').fetchone()[0], 2)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(originals, [path.read_bytes() for path in [self.research, self.ledger]])
            self.assertFalse(list(self.state.glob('snapshot-*')))
            self.assertFalse((self.state / 'before-retention.tar.gz.partial').exists())

    def test_backup_low_space_and_existing_partial_preserve_previous_generation(self):
        archive = self.state / 'before-retention.tar.gz'
        archive.write_bytes(b'previous archive')
        self.disk.return_value.free = 0
        with self.assertRaisesRegex(RuntimeError, 'headroom'):
            storage.protected_backup([self.research, self.ledger], self.state)
        self.assertEqual(archive.read_bytes(), b'previous archive')
        self.disk.return_value.free = 8 * 1024 ** 3
        partial = self.state / 'before-retention.tar.gz.partial'
        partial.write_bytes(b'someone else partial')
        with self.assertRaisesRegex(RuntimeError, 'target_unavailable'):
            storage.protected_backup([self.research, self.ledger], self.state)
        self.assertEqual(partial.read_bytes(), b'someone else partial')
        self.assertEqual(archive.read_bytes(), b'previous archive')

    def test_failed_archive_write_removes_only_owned_partial(self):
        archive = self.state / 'before-retention.tar.gz'
        archive.write_bytes(b'previous archive')
        with patch.object(storage.tarfile, 'open', side_effect=OSError('fixture archive write failed')):
            with self.assertRaises(OSError):
                storage.protected_backup([self.research, self.ledger], self.state)
        self.assertEqual(archive.read_bytes(), b'previous archive')
        self.assertFalse((self.state / 'before-retention.tar.gz.partial').exists())
        self.assertFalse(list(self.state.glob('snapshot-*')))


if __name__ == '__main__':
    unittest.main()
