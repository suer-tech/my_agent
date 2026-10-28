"""Private daily SQLite backup with bounded retention and conservative headroom."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RESERVE_BYTES = 512 * 1024**2
DATABASES = (
    ('data/local-finance.sqlite3', 'finance.sqlite3', True),
    ('data/personal-skills/state.sqlite3', 'personal-skills.sqlite3', False),
    ('data/tender-agent.sqlite3', 'tender-agent.sqlite3', False),
    ('data/disk-storage/audit.sqlite3', 'disk-audit.sqlite3', False),
    ('data/demand-research.sqlite3', 'demand-research.sqlite3', False),
    ('data/recovery-notices/notices.sqlite3', 'recovery-notices.sqlite3', False),
)
TREES = ('data/documents', 'data/iva-pilot/app/vault', 'data/iva-telegram/turns',
         'data/iva-pilot/app/data/memory', 'data/tender-research', 'data/demand-research',
         'data/iva-pilot/app/data/custom/agent/skills')
FILES = ('data/iva-runtime.json', 'data/iva-agents.json', 'data/iva-model.json',
         'data/iva-telegram/session.json')


class BackupError(RuntimeError):
    pass


@contextmanager
def backup_lock(output):
    """The timer and manual invocations share this existing-directory lock."""
    descriptor = os.open(output / '.backup.lock', os.O_RDWR | os.O_CREAT |
                         getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupError('Backup lock must be a regular file')
        if os.name == 'posix':
            import fcntl
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BackupError('Another backup is running; existing archives preserved') from None
        yield
    finally:
        os.close(descriptor)


def database_inputs():
    result = []
    for rel, temporary_name, required in DATABASES:
        path = ROOT / rel
        if not path.is_file():
            if required:
                raise BackupError('Required backup database is unavailable')
            continue
        # Committed WAL pages may not be in the main file yet. Main+WAL is a
        # conservative bound on the temporary SQLite snapshot, not exact growth.
        wal = path.with_name(path.name + '-wal')
        size = path.stat().st_size + (wal.stat().st_size if wal.is_file() else 0)
        result.append((rel, temporary_name, size))
    return result


def tree_estimate(path):
    """Bound tar payload without reading contents or following symlinks."""
    pending = [Path(path)]; payload = 0; entries = 0
    while pending:
        current = pending.pop(); info = current.lstat(); entries += 1
        if stat.S_ISREG(info.st_mode):
            payload += ((info.st_size + 511) // 512) * 512
        elif stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as scan:
                pending.extend(Path(item.path) for item in scan)
    # Extra tar/PAX records, names, metadata and block alignment per entry.
    return payload + entries * 8192


def space_plan(output, temporary_root, databases):
    snapshot_bytes = sum(item[2] for item in databases)
    archive_payload = snapshot_bytes + len(databases) * 8192
    for rel in TREES:
        if (ROOT / rel).exists():
            archive_payload += tree_estimate(ROOT / rel)
    for rel in FILES:
        if (ROOT / rel).is_file():
            archive_payload += tree_estimate(ROOT / rel)
    # Never assume favorable compression for admission: incompressible bytes
    # can expand. Old archives stay while ALL temporary DB snapshots AND the
    # new archive coexist. Separate /tmp filesystems need their own reserve.
    archive_bytes = (archive_payload * 101 + 99) // 100 + 1024**2
    same = output.stat().st_dev == temporary_root.stat().st_dev
    output_required = archive_bytes + (snapshot_bytes if same else 0) + RESERVE_BYTES
    temporary_required = 0 if same else snapshot_bytes + RESERVE_BYTES
    return {'snapshot_bytes': snapshot_bytes, 'archive_bound_bytes': archive_bytes,
            'same_filesystem': same, 'output_required_bytes': output_required,
            'temporary_required_bytes': temporary_required, 'reserve_bytes': RESERVE_BYTES}


def preflight(output, temporary_root, databases):
    plan = space_plan(output, temporary_root, databases)
    free = shutil.disk_usage(output).free
    if free < plan['output_required_bytes']:
        raise BackupError('Insufficient backup disk space: required_bytes=' + str(plan['output_required_bytes']) +
                          ', available_bytes=' + str(free) + '; existing archives preserved')
    if not plan['same_filesystem']:
        free = shutil.disk_usage(temporary_root).free
        if free < plan['temporary_required_bytes']:
            raise BackupError('Insufficient temporary disk space: required_bytes=' + str(plan['temporary_required_bytes']) +
                              ', available_bytes=' + str(free) + '; existing archives preserved')
    return plan


def copy_database(source, target):
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, \
         closing(sqlite3.connect(target)) as copied:
        original.backup(copied)
        if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise BackupError('Backup database integrity failed')


def remove_owned_partial(path, identity):
    """Remove this invocation's file, never another run's stale partial."""
    if identity is None:
        return
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        path.unlink()


def main():
    output = ROOT / 'data/server-backups'
    if output.is_symlink():
        raise BackupError('Backup directory cannot be a symlink')
    output.mkdir(mode=0o700, exist_ok=True)
    with backup_lock(output):
        now = datetime.now(timezone.utc)
        target = output / ('iva-' + now.strftime('%Y%m%d-%H%M%S') + '.tar.gz')
        temporary = target.with_suffix('.partial')
        if any(path.exists() or path.is_symlink() for path in (target, temporary)):
            raise BackupError('Backup timestamp already exists; no files overwritten')
        databases = database_inputs()
        temporary_root = Path(tempfile.gettempdir())
        preflight(output, temporary_root, databases)
        identity = None
        try:
            with tempfile.TemporaryDirectory(prefix='iva-backup-', dir=temporary_root) as directory:
                for rel, name, _ in databases:
                    copy_database(ROOT / rel, Path(directory) / name)
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
                info = os.fstat(descriptor); identity = (info.st_dev, info.st_ino)
                with os.fdopen(descriptor, 'wb') as stream:
                    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                        for rel, name, _ in databases:
                            archive.add(Path(directory) / name, arcname=rel)
                        for rel in TREES:
                            if (ROOT / rel).exists():
                                archive.add(ROOT / rel, arcname=rel)
                        for rel in FILES:
                            if (ROOT / rel).is_file():
                                archive.add(ROOT / rel, arcname=rel)
                    stream.flush(); os.fsync(stream.fileno())
            # Atomic no-overwrite publication on the same filesystem.
            os.link(temporary, target)
            remove_owned_partial(temporary, identity)
            identity = None
        finally:
            remove_owned_partial(temporary, identity)
        # Retention is unchanged; prune only after a successful new copy.
        cutoff = (now - timedelta(days=14)).timestamp()
        for path in output.glob('iva-????????-??????.tar.gz'):
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
        print('Private backup verified: ' + target.name)


if __name__ == '__main__':
    main()
