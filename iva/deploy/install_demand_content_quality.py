"""Install reviewed research-reader fixes while only the scheduler is paused.

Run staged source as root from /opt/iva. RELEASE/source holds FILES;
expected-base.json maps absolute targets to prior SHA256/null and
source-sha256.json maps FILES to reviewed staged SHA256. No root helper, unit,
gateway, Telegram, research evidence or schedule configuration is replaced.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
import time

ROOT = Path('/opt/iva')
RELEASE = ROOT / 'data/demand-content-quality-release-20261025-r2'
SCHEDULER = 'iva@scheduler.service'
REQUIRED_ATTEMPT = 2
FILES = [
    'iva_integration/demand_content_quality.py', 'iva_integration/demand_agents.py',
    'iva_integration/demand_scope.py', 'iva_integration/demand_research.py',
    'iva_integration/demand_sources.py', 'deploy/run_supervised_demand.py',
    'tests/test_demand_content_quality.py', 'tests/test_demand_agents.py',
    'tests/test_demand_request_keys.py', 'tests/test_demand_scope.py',
    'tests/test_demand_sources.py', 'tests/test_run_supervised_demand.py',
    'tests/fixtures/demand_content/docs_sidebar_case.html',
    'tests/fixtures/demand_content/path_shell.html',
    'tests/fixtures/demand_content/short_forum_comment.html',
    'deploy/install_demand_content_quality.py', 'tests/test_install_demand_content_quality.py',
    'deploy/verify_demand_request_keys.py', 'tests/test_verify_demand_request_keys.py',
]
PATTERNS = ('test_demand_content_quality.py', 'test_demand_sources.py', 'test_demand_agents.py',
            'test_demand_request_keys.py', 'test_demand_scope.py',
            'test_run_supervised_demand.py', 'test_install_demand_content_quality.py',
            'test_verify_demand_request_keys.py')


def run(args, **kwargs):
    return subprocess.run(args, check=True, **{'capture_output': True, 'text': True, 'timeout': 15, **kwargs})


def safe(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise RuntimeError('Symlink release path refused')


def digest(path):
    safe(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError('Regular release file required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    old = json.loads((RELEASE / 'expected-base.json').read_text())
    new = json.loads((RELEASE / 'source-sha256.json').read_text())
    if set(old) != {str(ROOT / name) for name in FILES} or set(new) != set(FILES):
        raise RuntimeError('Incomplete reviewed manifest')
    for name in FILES:
        if digest(ROOT / name) != old[str(ROOT / name)]:
            raise RuntimeError('Runtime source changed since review: ' + name)
        hashed = digest(RELEASE / 'source' / name)
        if hashed is None or hashed != new[name]:
            raise RuntimeError('Staged source changed since review: ' + name)
    return old, new


def active():
    value = run(['systemctl', 'show', SCHEDULER, '--property=ActiveState', '--value'], timeout=2).stdout.strip()
    if value not in ('active', 'inactive', 'deactivating'):
        raise RuntimeError('Unexpected scheduler activity; preserve for review')
    return value


def coordinator_idle():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect('/run/iva-browser/control.sock')
        client.sendall(b'{"action":"research-status"}\n')
        data = b''
        while b'\n' not in data:
            part = client.recv(4096)
            if not part or len(data) + len(part) > 16384:
                raise RuntimeError('Research coordinator unavailable')
            data += part
    value = json.loads(data.split(b'\n', 1)[0])
    return (isinstance(value, dict) and value.get('ok') is True and value.get('status') == 'idle'
            and value.get('active') is False and value.get('granted') is False)


def standalone_finished():
    result = run(['systemctl', 'list-units', '--all', '--type=service',
                  '--state=activating,active,deactivating', '--plain', '--no-legend', '--no-pager',
                  'iva-demand-supervised*'], timeout=2)
    if result.stdout.strip():
        return False
    path = ROOT / ('data/demand-supervised-20261025-attempt' + str(REQUIRED_ATTEMPT)) / 'state.json'
    safe(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        return False
    state = json.loads(path.read_text())
    return (isinstance(state, dict) and state.get('version') == 1
            and state.get('attempt') == REQUIRED_ATTEMPT
            and state.get('state') in ('completed', 'completed_with_gaps', 'failed'))


def idle(db):
    for query in (
        "SELECT count(*) FROM local_updates WHERE state='processing'",
        "SELECT count(*) FROM scheduled_jobs WHERE state='running'",
        "SELECT count(*) FROM scheduled_runs WHERE state='running'",
        "SELECT count(*) FROM scheduled_outbox WHERE state='sending'",
    ):
        if db.execute(query).fetchone()[0]:
            return False
    return coordinator_idle() and standalone_finished()


def require_idle():
    with closing(sqlite3.connect((ROOT / 'data/local-finance.sqlite3').as_uri() + '?mode=ro',
                                 uri=True, timeout=2)) as db:
        if not idle(db):
            raise RuntimeError('Owner, schedule, delivery or research active; defer installation')


def pause_scheduler(mark_requested):
    """No long poll: scheduler uses default SIGTERM and exits at once when idle.

    Both Store.claim and Store.deliver must commit a running/sending transition
    before work; the short writer barrier prevents new admission during stop.
    """
    with closing(sqlite3.connect((ROOT / 'data/local-finance.sqlite3').as_uri() + '?mode=rw',
                                 uri=True, timeout=0.2, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            if not idle(db):
                raise RuntimeError('Work began before scheduler pause; defer installation')
            mark_requested()
            deadline = time.monotonic() + 5
            run(['systemctl', '--no-block', 'stop', SCHEDULER], timeout=2)
            while active() != 'inactive':
                if time.monotonic() >= deadline:
                    raise RuntimeError('Scheduler stop not confirmed; never force-kill')
                time.sleep(0.05)
        finally:
            db.execute('ROLLBACK')


def protected():
    with closing(sqlite3.connect((ROOT / 'data/local-finance.sqlite3').as_uri() + '?mode=ro',
                                 uri=True, timeout=2)) as db:
        return {key: hashlib.sha256(json.dumps(db.execute(query).fetchall()).encode()).hexdigest()
                for key, query in {
                    'operations': 'SELECT * FROM operations ORDER BY id',
                    'schedule_specs': 'SELECT id,spec FROM scheduled_jobs ORDER BY id',
                    'engine': "SELECT key,value FROM local_meta WHERE key='primary_engine'",
                }.items()}


def replace(source, target, uid, gid, mode):
    safe(source)
    safe(target)
    missing = []
    parent = target.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        os.chown(directory, uid, gid)
    descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.reader-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(name, target)
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(name).unlink(missing_ok=True)


def preflight():
    environment = {**os.environ, 'PYTHONPATH': str(ROOT) + ':' + str(ROOT / 'deploy'),
                   'PYTHONDONTWRITEBYTECODE': '1'}
    with (RELEASE / 'tests.log').open('wb') as output:
        for pattern in PATTERNS:
            subprocess.run(['runuser', '-u', 'iva', '--', str(ROOT / '.venv/bin/python'),
                            '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v'],
                           cwd=RELEASE / 'source', env=environment, stdout=output,
                           stderr=subprocess.STDOUT, check=True, timeout=180)


def start_scheduler():
    run(['systemctl', 'start', SCHEDULER])
    if active() != 'active':
        raise RuntimeError('Scheduler activity not restored')


def new_backup():
    """Preserve every prior attempt, including an idle-admission race.

    install() verifies all original source hashes again before reaching here, so
    a retry cannot hide a partial deployment by replacing its first backup.
    """
    for number in range(1, 100):
        path = RELEASE / ('before' if number == 1 else 'before-' + str(number))
        safe(path)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            if not path.is_dir():
                raise RuntimeError('Unexpected backup path; preserve for review')
            continue
        return path
    raise RuntimeError('Too many retained deployment attempts; review before retrying')


def install(account):
    old, new = verify()
    require_idle()
    preflight()
    verify()
    require_idle()
    prior = active()
    if prior not in ('active', 'inactive'):
        raise RuntimeError('Scheduler is transitioning; defer installation')
    enablement = run(['systemctl', 'show', SCHEDULER, '--property=UnitFileState', '--value']).stdout.strip()
    before = new_backup()
    attributes = {}
    for index, name in enumerate(FILES):
        path = ROOT / name
        if path.exists():
            info = path.stat()
            attributes[name] = [index, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
            shutil.copy2(path, before / str(index))
    (before / 'state.json').write_text(json.dumps({'active': prior, 'enabled': enablement,
                                                 'attributes': attributes}, indent=2))
    requested = False
    changed = False
    replaced = []
    resume_safe = True

    def mark_requested():
        nonlocal requested
        requested = True

    try:
        if prior == 'active':
            pause_scheduler(mark_requested)
        require_idle()
        original = protected()
        verify()
        changed = True
        for name in FILES:
            source, target = RELEASE / 'source' / name, ROOT / name
            if digest(source) != new[name] or digest(target) != old[str(target)]:
                raise RuntimeError('Source changed while installing: ' + name)
            replaced.append(name)
            replace(source, target, account.pw_uid, account.pw_gid, 0o644)
        if protected() != original:
            raise RuntimeError('Financial data or schedule specifications changed')
        hashes = {name: digest(ROOT / name) for name in FILES}
        if hashes != new:
            raise RuntimeError('Installed source differs from reviewed source')
        if requested:
            start_scheduler()
            requested = False
        if run(['systemctl', 'show', SCHEDULER, '--property=UnitFileState', '--value']).stdout.strip() != enablement:
            raise RuntimeError('Scheduler enablement changed')
        report = {'installed': True, 'scheduler_previous_active': prior,
                  'supervised_attempt_completed': REQUIRED_ATTEMPT,
                  'scheduler_enablement_preserved': enablement, 'tests_passed': True,
                  'financial_operations_unchanged': True, 'schedule_specs_unchanged': True,
                  'gateway_telegram_engine_untouched': True, 'no_root_helper_changes': True,
                  'sha256': hashes, 'rollback': str(before)}
        (RELEASE / 'installation.json').write_text(json.dumps(report, indent=2))
        return report
    except BaseException as original_error:
        errors = []
        if changed:
            # A failed start/report may have already resumed research. Never
            # interrupt that work just to roll code back; preserve reviewed files.
            if active() != 'inactive':
                try:
                    pause_scheduler(mark_requested)
                except Exception as error:
                    raise RuntimeError('Rollback deferred: scheduler is busy; reviewed source preserved') from error
            for name in reversed(replaced):
                try:
                    if name in attributes:
                        index, uid, gid, mode = attributes[name]
                        replace(before / str(index), ROOT / name, uid, gid, mode)
                    else:
                        (ROOT / name).unlink(missing_ok=True)
                except Exception as error:
                    errors.append(type(error).__name__)
            # Never start a mixture of old and new modules after an I/O failure.
            try:
                resume_safe = all(digest(ROOT / name) == old[str(ROOT / name)] for name in FILES)
            except Exception:
                resume_safe = False
            if not resume_safe:
                errors.append('RollbackSourceMismatch')
        if requested and prior == 'active' and resume_safe:
            try:
                start_scheduler()
                requested = False
            except Exception as error:
                errors.append(type(error).__name__)
        (RELEASE / 'installation.json').unlink(missing_ok=True)
        if errors:
            raise RuntimeError('Reader installation failed; rollback needs review: ' + ','.join(errors)) from original_error
        raise
    finally:
        if requested and prior == 'active' and resume_safe:
            start_scheduler()


def main():
    if os.name != 'posix' or os.geteuid() != 0 or Path.cwd() != ROOT:
        raise RuntimeError('Run as root from /opt/iva')
    import fcntl
    import pwd
    lock_path = ROOT / 'data/application-deploy.lock'
    safe(lock_path)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = install(pwd.getpwnam('iva'))
    print(json.dumps({key: value for key, value in report.items() if key != 'sha256'}))


if __name__ == '__main__':
    main()
