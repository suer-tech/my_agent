"""Bound research scratch history while preserving findings and live checkpoints."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'data/demand-storage'
RESEARCH = ROOT / 'data/demand-research.sqlite3'
LEDGER = ROOT / 'data/local-finance.sqlite3'
RETENTION_DAYS = 14
MIN_NEW_CYCLE_FREE = 1024 ** 3
BACKUP_RESERVE = 512 * 1024 ** 2


def storage_status():
    try:
        path = STATE / 'status.json'
        if path.stat().st_size > 128 * 1024:
            return {'state': 'status_unavailable'}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {'state': 'status_unavailable'}
    except (OSError, ValueError):
        return {'state': 'not_checked', 'retention_days': RETENTION_DAYS,
                'new_cycle_min_free_bytes': MIN_NEW_CYCLE_FREE}


def admission(cursor, directory=None):
    """Reserve space before a new cycle; never interrupt an anchored investigation."""
    if cursor.get('demand_cycle_id') or cursor.get('demand_pending_result'):
        return {'allowed': True, 'reason': 'existing_cycle'}
    try:
        free = shutil.disk_usage(directory or ROOT / 'data').free
    except OSError:
        return {'allowed': False, 'reason': 'storage_unknown'}
    return {'allowed': free >= MIN_NEW_CYCLE_FREE,
            'reason': 'space_available' if free >= MIN_NEW_CYCLE_FREE else 'storage_low',
            'free_bytes': free, 'required_free_bytes': MIN_NEW_CYCLE_FREE}


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path, create=False):
    import fcntl
    if path.is_symlink():
        raise OSError('unsafe_lock')
    with path.open('a' if create else 'rb') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def owner_or_schedule_busy(ledger=LEDGER):
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        return bool(db.execute("SELECT count(*) FROM local_updates WHERE state='processing'").fetchone()[0]
                    or db.execute("SELECT count(*) FROM scheduled_jobs WHERE state='running'").fetchone()[0])


def protected_backup(paths, directory):
    """One replaceable compressed generation, outside the daily backup includes."""
    directory = Path(directory)
    target = directory / 'before-retention.tar.gz'
    partial = directory / 'before-retention.tar.gz.partial'
    if target.is_symlink() or partial.exists() or partial.is_symlink():
        raise RuntimeError('backup_target_unavailable')
    # Two consistent DB copies and an incompressible new archive coexist.
    estimate = sum(p.stat().st_size + (p.with_name(p.name + '-wal').stat().st_size
                   if p.with_name(p.name + '-wal').exists() else 0) for p in paths)
    if shutil.disk_usage(directory).free < 2 * estimate + BACKUP_RESERVE:
        raise RuntimeError('backup_headroom_low')
    own_partial = False
    try:
        with tempfile.TemporaryDirectory(prefix='snapshot-', dir=directory) as temporary:
            files = []
            for index, source in enumerate(paths):
                copy = Path(temporary) / ('research.sqlite3' if index == 0 else 'schedules.sqlite3')
                original = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=3)
                destination = sqlite3.connect(copy)
                try:
                    original.backup(destination)
                    if destination.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise RuntimeError('backup_integrity_failed')
                finally:
                    destination.close(); original.close()
                files.append(copy)
            descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            own_partial = True
            with os.fdopen(descriptor, 'wb') as out:
                with tarfile.open(fileobj=out, mode='w:gz') as archive:
                    for file in files:
                        archive.add(file, arcname=file.name)
                out.flush(); os.fsync(out.fileno())
        os.replace(partial, target)
        return {'bytes': target.stat().st_size, 'generations': 1}
    finally:
        if own_partial:
            partial.unlink(missing_ok=True)


def reap_backup_temporary(directory):
    """Called under maintenance.lock; remove only this runner's crash leftovers."""
    directory = Path(directory).resolve(strict=True)
    removed = 0
    partial = directory / 'before-retention.tar.gz.partial'
    if partial.exists() and stat.S_ISREG(partial.lstat().st_mode):
        partial.unlink(); removed += 1
    allowed = {name + suffix for name in ('research.sqlite3', 'schedules.sqlite3')
               for suffix in ('', '-wal', '-shm', '-journal')}
    for candidate in directory.glob('snapshot-*'):
        if not stat.S_ISDIR(candidate.lstat().st_mode):
            continue
        children = list(candidate.iterdir())
        if any(child.name not in allowed or not stat.S_ISREG(child.lstat().st_mode) for child in children):
            continue
        for child in children:
            child.unlink(); removed += 1
        candidate.rmdir()
    return removed


def reclaim_research_pages(path):
    """Never VACUUM the financial database; it reuses freed scheduler pages."""
    with closing(sqlite3.connect(path, timeout=0.25)) as db:
        page = db.execute('PRAGMA page_size').fetchone()[0]
        count = db.execute('PRAGMA page_count').fetchone()[0]
        free = db.execute('PRAGMA freelist_count').fetchone()[0]
        if free * page < 32 * 1024 ** 2 or free < count / 4:
            return {'vacuum': False, 'reusable_bytes': free * page}
        if shutil.disk_usage(path.parent).free < 2 * count * page + BACKUP_RESERVE:
            return {'vacuum': False, 'reason': 'headroom', 'reusable_bytes': free * page}
        started = time.monotonic()
        db.set_progress_handler(lambda: int(time.monotonic() - started > 12), 10000)
        try:
            db.execute('VACUUM')
            return {'vacuum': True, 'reusable_bytes_before': free * page}
        except sqlite3.OperationalError:
            return {'vacuum': False, 'reason': 'busy_or_time_budget', 'reusable_bytes': free * page}


def brief(result):
    keys = ('ok', 'dry_run', 'cycles_compacted', 'source_bodies_pruned', 'source_bytes_released',
            'logical_bytes_before', 'logical_bytes_after', 'logical_bytes_reclaimed',
            'eligible_cycles', 'eligible_source_bodies', 'more_eligible', 'compacted',
            'candidate_rows', 'updated_rows', 'estimated_savings_bytes', 'reclaimed_logical_bytes',
            'original_bytes', 'compacted_bytes', 'protected_run_count', 'skipped')
    return {key: result[key] for key in keys if key in result}


def maintain():
    from iva_integration import research_priority, demand_schedule_retention as schedules
    from iva_integration.demand_retention import compact
    from iva_integration.demand_research import Research
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    clock = datetime.now(timezone.utc)
    prior = storage_status()
    report = {key: prior[key] for key in ('last_compaction_at', 'last_compaction', 'backup', 'more_eligible') if key in prior}
    report.update(checked_at=clock.isoformat(), retention_days=RETENTION_DAYS,
                  free_bytes=shutil.disk_usage(ROOT / 'data').free,
                  research_file_bytes=RESEARCH.stat().st_size, new_cycle_min_free_bytes=MIN_NEW_CYCLE_FREE)

    def finish(state):
        report['state'] = state
        atomic_json(STATE / 'status.json', report)
        return report

    with lock(STATE / 'maintenance.lock', create=True):
        report['temporary_files_removed'] = reap_backup_temporary(STATE)
        last = prior.get('last_compaction_at')
        if last and not prior.get('more_eligible'):
            try:
                if clock - datetime.fromisoformat(last) < timedelta(hours=23):
                    return finish('checked_not_due')
            except (ValueError, TypeError):
                pass
        try:
            with lock(ROOT / 'data/application-deploy.lock'), lock(ROOT / 'data/server-backups/.backup.lock', create=True):
                status = research_priority.request({'action': 'research-status'})
                if status.get('status') != 'idle' or status.get('active') or owner_or_schedule_busy():
                    return finish('waiting_for_idle')
                # Ask normal arbitration for a safe browser boundary. An active
                # investigation or interactive browser is never interrupted.
                try:
                    deadline = time.monotonic() + 25
                    while True:
                        granted = research_priority.acquire('demand-storage-maintenance', 'retention')
                        if granted.get('ok') and granted.get('granted'):
                            break
                        if time.monotonic() >= deadline:
                            return finish('waiting_for_browser_boundary')
                        time.sleep(2)
                    if owner_or_schedule_busy():
                        return finish('waiting_for_idle')
                    cutoff = clock - timedelta(days=RETENTION_DAYS)
                    ledger = sqlite3.connect(LEDGER, timeout=2)
                    try:
                        schedule_plan = schedules.plan(ledger, cutoff, now=clock)
                        if schedule_plan.get('skipped', {}).get('job_cursor_invalid'):
                            return finish('blocked_invalid_checkpoint')
                        protected = schedule_plan['protected_cycle_ids']
                        store = Research(RESEARCH)
                        try:
                            research_plan = compact(store, clock=clock, protected_cycle_ids=protected, dry_run=True)
                            report['planned'] = {'research': brief(research_plan), 'schedules': brief(schedule_plan)}
                            changes = (research_plan.get('cycles_compacted', 0) or research_plan.get('source_bodies_pruned', 0)
                                       or schedule_plan.get('candidate_rows', 0))
                            if changes:
                                report['backup'] = protected_backup([RESEARCH, LEDGER], STATE)
                                if owner_or_schedule_busy():
                                    return finish('waiting_for_idle')
                                research_result = compact(store, clock=clock, protected_cycle_ids=protected, dry_run=False)
                                schedule_results = []
                                report['last_compaction'] = {'research': brief(research_result), 'schedules': schedule_results}
                                report['last_compaction_at'] = clock.isoformat()
                                report['more_eligible'] = True
                                finish('continuation_pending')
                                for _ in range(8):
                                    if owner_or_schedule_busy():
                                        report['more_eligible'] = True
                                        return finish('continuation_pending')
                                    result = schedules.apply(ledger, cutoff, now=clock)
                                    schedule_results.append(brief(result))
                                    finish('continuation_pending')
                                    if not result.get('more_eligible'):
                                        break
                                report['last_compaction'] = {'research': brief(research_result), 'schedules': schedule_results}
                                report['more_eligible'] = bool(research_result.get('more_eligible') or result.get('more_eligible'))
                            else:
                                report['last_compaction'] = {'research': brief(research_plan), 'schedules': brief(schedule_plan)}
                                report['more_eligible'] = False
                        finally:
                            store.close()
                    finally:
                        ledger.close()
                    if changes and not owner_or_schedule_busy():
                        report['research_pages'] = reclaim_research_pages(RESEARCH)
                    report['last_compaction_at'] = clock.isoformat()
                    report['free_bytes'] = shutil.disk_usage(ROOT / 'data').free
                    return finish('compacted' if changes else 'no_old_material')
                finally:
                    if not research_priority.release():
                        report['release_pending'] = True
                        atomic_json(STATE / 'status.json', report)
        except BlockingIOError:
            return finish('waiting_for_lock')


def main():
    if os.name != 'posix' or ROOT != Path('/opt/iva'):
        raise RuntimeError('retention_runs_only_on_active_vps')
    try:
        print(json.dumps(maintain(), ensure_ascii=False))
    except Exception as error:
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = storage_status()
        value.update(checked_at=datetime.now(timezone.utc).isoformat(), state='maintenance_error',
                     error_type=type(error).__name__)
        atomic_json(STATE / 'status.json', value)
        print(json.dumps({'state': 'maintenance_error', 'error_type': type(error).__name__}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
