"""Install verified-recovery notices at an idle boundary, without stopping Iva.

Stage reviewed FILES under RELEASE/source. expected-base.json maps every absolute
runtime target (including EXTERNAL) to its prior SHA256 or null. source-sha256.json
maps every FILES relative path to the reviewed staged SHA256. Run this staged
script as root from /opt/iva. It never sends a setup notice or restarts OpenCode.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
import time
import urllib.request


ROOT = Path('/opt/iva')
RELEASE = ROOT / 'data/recovery-notices-release-20261025-r3'
NOTICES = ROOT / 'data/recovery-notices'
EVENTS = Path('/var/lib/iva-opencode-health-events')
SERVICE = 'iva-recovery-notices.service'
TIMER = 'iva-recovery-notices.timer'
WATCHDOG_SERVICE = 'iva-opencode-health.service'
WATCHDOG_TIMER = 'iva-opencode-health.timer'
GATEWAY = 'iva@gateway.service'
TELEGRAM = 'iva@telegram.service'
FILES = [
    'iva_integration/recovery_notices.py', 'iva_integration/recovery_threads.py',
    'iva_integration/recovery_schedules.py', 'iva_integration/recovery_opencode.py',
    'tests/test_recovery_notices.py', 'tests/test_recovery_threads.py',
    'tests/test_recovery_schedules.py', 'tests/test_recovery_opencode.py',
    'deploy/opencode_health.py', 'tests/test_opencode_health.py',
    'iva_integration/browser_control.py', 'deploy/backup.py',
    'tests/test_backup_capacity.py', 'tests/test_browser_control_recovery.py',
    'deploy/install_recovery_notices.py',
    'tests/test_recovery_install.py',
    'deploy/systemd/iva-recovery-notices.service',
    'deploy/systemd/iva-recovery-notices.timer',
    'deploy/systemd/iva-opencode-health.service',
    'docs/RECOVERY-NOTICES.ru.md',
]
EXTERNAL = {
    '/usr/local/lib/iva/opencode_health.py': 'deploy/opencode_health.py',
    '/etc/systemd/system/' + WATCHDOG_SERVICE: 'deploy/systemd/' + WATCHDOG_SERVICE,
    '/etc/systemd/system/' + SERVICE: 'deploy/systemd/' + SERVICE,
    '/etc/systemd/system/' + TIMER: 'deploy/systemd/' + TIMER,
}


def run(args, **kwargs):
    options = {'capture_output': True, 'text': True, 'timeout': 30, **kwargs}
    return subprocess.run(args, check=True, **options)


def safe_path(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise RuntimeError('Symlink deployment target refused')


def digest(path):
    safe_path(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError('Regular deployment source required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mapping():
    return {str(ROOT / rel): rel for rel in FILES} | EXTERNAL


def verify_sources():
    expected = json.loads((RELEASE / 'expected-base.json').read_text())
    reviewed = json.loads((RELEASE / 'source-sha256.json').read_text())
    targets = mapping()
    if set(expected) != set(targets) or set(reviewed) != set(FILES):
        raise RuntimeError('Incomplete release manifests')
    for target in targets:
        if digest(Path(target)) != expected[target]:
            raise RuntimeError('Runtime source changed since review: ' + target)
    for rel in FILES:
        hashed = digest(RELEASE / 'source' / rel)
        if hashed is None or hashed != reviewed[rel]:
            raise RuntimeError('Staged source changed since review: ' + rel)
    return targets, reviewed


def unit_state(verb, unit):
    result = subprocess.run(['systemctl', verb, unit], capture_output=True, text=True, timeout=10)
    value = result.stdout.strip()
    if value:
        return value
    loaded = subprocess.run(['systemctl', 'show', '--property=LoadState', '--value', unit],
                            capture_output=True, text=True, timeout=10)
    if loaded.returncode == 0 and loaded.stdout.strip() == 'not-found':
        return 'not-found' if verb == 'is-enabled' else 'unknown'
    raise RuntimeError('Cannot establish unit state: ' + unit)


def timer_state(unit):
    value = {'enabled': unit_state('is-enabled', unit), 'active': unit_state('is-active', unit)}
    if value['enabled'] not in ('enabled', 'disabled', 'not-found') or value['active'] not in ('active', 'inactive', 'unknown'):
        raise RuntimeError('Unexpected timer state; preserve it for review: ' + unit)
    return value


def require_inactive(unit):
    if unit_state('is-active', unit) not in ('inactive', 'unknown'):
        raise RuntimeError('Service is busy or failed; do not interrupt it: ' + unit)


def quiet():
    """Only reads owner/schedule state; no schedule claim or maintenance pause."""
    with closing(sqlite3.connect((ROOT / 'data/local-finance.sqlite3').as_uri() + '?mode=ro',
                                 uri=True, timeout=2)) as db:
        if (db.execute("SELECT count(*) FROM local_updates WHERE state='processing'").fetchone()[0]
                or db.execute("SELECT count(*) FROM scheduled_jobs WHERE state='running'").fetchone()[0]
                or db.execute("SELECT count(*) FROM scheduled_runs WHERE state='running'").fetchone()[0]):
            return False
        # Future slots remain queued in next_at. The guarded Telegram pause
        # prevents new claims while runtime files change; an already running
        # research cycle remains an unconditional refusal above and below.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect('/run/iva-browser/control.sock')
        client.sendall(b'{"action":"research-status"}\n')
        data = b''
        while b'\n' not in data:
            part = client.recv(4096)
            if not part or len(data) + len(part) > 16384:
                raise RuntimeError('Coordinator status unavailable')
            data += part
    result = json.loads(data.split(b'\n', 1)[0])
    return (isinstance(result, dict) and result.get('ok') is True and result.get('status') == 'idle'
            and result.get('active') is False and result.get('granted') is False)


def require_quiet():
    if not quiet():
        raise RuntimeError('Owner/research work active or imminent; defer installation')


def protected_snapshot():
    with closing(sqlite3.connect((ROOT / 'data/local-finance.sqlite3').as_uri() + '?mode=ro',
                                 uri=True, timeout=2)) as db:
        values = {name: hashlib.sha256(json.dumps(db.execute(sql).fetchall()).encode()).hexdigest()
                  for name, sql in {
                      'operations': 'SELECT * FROM operations ORDER BY id',
                      'schedule_specs': 'SELECT id,spec FROM scheduled_jobs ORDER BY id',
                      'selected_engine': "SELECT key,value FROM local_meta WHERE key='primary_engine'",
                  }.items()}
    values['model_profiles'] = {rel: digest(ROOT / rel) for rel in
                               ('data/iva-agents.json', 'data/opencode-primary.json')}
    return values


def pause_telegram():
    """Request graceful polling stop while owner admission is closed briefly.

    SIGTERM sets the transport's stop marker. A current long poll can take up to
    45 seconds to return, so release the database barrier immediately and await
    normal exit. Never kill the process or stop gateway before that exit.
    """
    database = ROOT / 'data/local-finance.sqlite3'
    with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True,
                                 timeout=0.2, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            require_quiet()
            run(['systemctl', '--no-block', 'stop', TELEGRAM], timeout=2)
        finally:
            db.execute('ROLLBACK')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        observed = unit_state('is-active', TELEGRAM)
        if observed == 'inactive':
            return
        if observed in ('failed', 'unknown'):
            raise RuntimeError('Telegram did not stop cleanly')
        time.sleep(0.25)
    raise RuntimeError('Telegram graceful stop still pending; do not interrupt work')


def restore_telegram():
    # Normal start does not change enablement, profile or transport settings.
    run(['systemctl', 'start', TELEGRAM])
    if unit_state('is-active', TELEGRAM) != 'active':
        raise RuntimeError('Telegram activity was not restored')


def restart_gateway():
    """Brief admission barrier; commits no financial or scheduling changes.

    Bot.process_one and Store.claim must write their processing/running state
    before executing work. BEGIN IMMEDIATE closes the race after the idle check.
    The gateway's startup and /health do not need a database writer.
    """
    config = json.loads((ROOT / 'data/iva-pilot/control.json').read_text())
    port = config.get('gateway_port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError('Gateway health port unavailable')
    database = ROOT / 'data/local-finance.sqlite3'
    with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True,
                                 timeout=0.2, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            deadline = time.monotonic() + 10
            require_quiet()

            def remaining():
                value = deadline - time.monotonic()
                if value <= 0:
                    raise RuntimeError('Gateway reload admission deadline exceeded')
                return value

            def observed():
                result = run(['systemctl', 'show', GATEWAY,
                              '--property=MainPID,InvocationID,ActiveState,SubState,Job'],
                             timeout=min(2, remaining()))
                fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
                if (set(fields) != {'MainPID', 'InvocationID', 'ActiveState', 'SubState', 'Job'}
                        or not re.fullmatch(r'[0-9a-f]{32}', fields.get('InvocationID', ''))
                        or not fields.get('MainPID', '').isdigit() or int(fields['MainPID']) <= 0):
                    return None
                return fields

            old = observed()
            if old is None or old.get('ActiveState') != 'active' or old.get('SubState') != 'running' or old.get('Job'):
                raise RuntimeError('Gateway was not stably active before reload')
            # Some systemctl clients wait after systemd has already replaced
            # the process. Queue the request and verify a NEW service invocation
            # plus HTTP readiness, without holding the admission barrier on that
            # client's transaction wait. Old /health success is never sufficient.
            run(['systemctl', '--no-block', 'restart', GATEWAY], timeout=min(2, remaining()))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while time.monotonic() < deadline:
                current = observed()
                if (current is not None and current['InvocationID'] != old['InvocationID']
                        and current['MainPID'] != old['MainPID'] and current.get('ActiveState') == 'active'
                        and current.get('SubState') == 'running' and not current.get('Job')):
                    try:
                        with opener.open('http://127.0.0.1:' + str(port) + '/health',
                                         timeout=min(1, remaining())) as response:
                            body = json.loads(response.read(4096))
                            if response.status == 200 and isinstance(body, dict) and body.get('ok') is True:
                                return
                    except (OSError, ValueError):
                        pass
                time.sleep(0.1)
            raise RuntimeError('Gateway health did not recover inside admission deadline')
        finally:
            db.execute('ROLLBACK')


def replace(source, target, uid, gid, mode):
    safe_path(source)
    safe_path(target)
    descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.notices-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def private_directory(path, uid, gid, mode):
    safe_path(path)
    if not path.exists():
        path.mkdir(mode=mode)
        os.chown(path, uid, gid)
        path.chmod(mode)
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode):
        raise RuntimeError('Unexpected persistent directory permissions: ' + str(path))


def preflight():
    stage = RELEASE / 'source'
    environment = {**os.environ, 'PYTHONPATH': str(ROOT) + ':' + str(ROOT / 'deploy'),
                   'PYTHONDONTWRITEBYTECODE': '1'}
    with (RELEASE / 'preflight-tests.log').open('wb') as output:
        for pattern in ('test_recovery*.py', 'test_opencode_health.py', 'test_backup_capacity.py',
                        'test_browser_control_recovery.py'):
            subprocess.run(['runuser', '-u', 'iva', '--', str(ROOT / '.venv/bin/python'),
                            '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v'],
                           cwd=stage, env=environment, stdout=output, stderr=subprocess.STDOUT,
                           check=True, timeout=180)
        # Isolated temp fixtures exercise the actual root-only export boundary.
        subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'unittest', 'discover',
                        '-s', 'tests', '-p', 'test_opencode_health.py', '-v'],
                       cwd=stage, env=environment, stdout=output, stderr=subprocess.STDOUT,
                       check=True, timeout=90)
    run(['systemd-analyze', 'verify', *[str(stage / rel) for rel in FILES
                                      if rel.startswith('deploy/systemd/')]])


def restore_timer(unit, before, existed=True):
    if before['enabled'] == 'enabled':
        run(['systemctl', 'enable', unit])
    elif existed:
        run(['systemctl', 'disable', unit])
    if before['active'] == 'active':
        run(['systemctl', 'start', unit])
    elif existed:
        run(['systemctl', 'stop', unit])


def install(account):
    """Caller holds application-deploy.lock; existing services stay available."""
    targets, reviewed = verify_sources()
    expected = json.loads((RELEASE / 'expected-base.json').read_text())
    require_quiet()
    preflight()
    # Tests may take time and collaborators may have edited either source tree.
    verify_sources()
    require_quiet()
    if unit_state('is-active', GATEWAY) != 'active':
        raise RuntimeError('Gateway must already be active')
    states = {unit: timer_state(unit) for unit in (WATCHDOG_TIMER, TIMER)}
    telegram = {'active': unit_state('is-active', TELEGRAM),
                'enabled': unit_state('is-enabled', TELEGRAM)}
    if telegram['active'] not in ('active', 'inactive'):
        raise RuntimeError('Telegram is already transitioning; defer installation')
    for unit in (WATCHDOG_SERVICE, SERVICE):
        require_inactive(unit)
    before = RELEASE / 'before'
    before.mkdir(mode=0o700)
    attributes = {}
    for index, target in enumerate(targets):
        path = Path(target)
        if path.exists():
            info = path.stat()
            attributes[target] = [index, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
            shutil.copy2(path, before / str(index))
    (before / 'state.json').write_text(json.dumps({'timers': states, 'telegram': telegram,
                                                 'attributes': attributes}, indent=2))
    protected = protected_snapshot()
    changed = False
    gateway_restarted = False
    telegram_paused = False
    stopped = []
    try:
        if telegram['active'] == 'active':
            telegram_paused = True
            pause_telegram()
        require_quiet()
        for unit in (WATCHDOG_TIMER, TIMER):
            if states[unit]['active'] == 'active':
                stopped.append(unit)
                run(['systemctl', 'stop', unit])
        # A timer may have fired just before stopping. Never stop its worker.
        for unit in (WATCHDOG_SERVICE, SERVICE):
            require_inactive(unit)
        verify_sources()
        require_quiet()
        private_directory(EVENTS, 0, account.pw_gid, 0o750)
        private_directory(NOTICES, account.pw_uid, account.pw_gid, 0o700)
        changed = True
        for target, rel in targets.items():
            source = RELEASE / 'source' / rel
            if digest(source) != reviewed[rel] or digest(Path(target)) != expected[target]:
                raise RuntimeError('Staged source changed during installation')
            external = target in EXTERNAL
            replace(source, Path(target), 0 if external else account.pw_uid,
                    0 if external else account.pw_gid, 0o644)
        run(['systemctl', 'daemon-reload'])
        require_quiet()
        gateway_restarted = True
        restart_gateway()
        if unit_state('is-active', GATEWAY) != 'active':
            raise RuntimeError('Gateway restart did not become active')
        # This creates only notifier cursor/outbox state, never sends Telegram.
        baseline = run(['runuser', '-u', 'iva', '--', str(ROOT / '.venv/bin/python'),
                        '-m', 'iva_integration.recovery_notices', '--collect-only'], cwd=ROOT)
        result = json.loads(baseline.stdout)
        if (result.get('delivery') != 'collect_only' or not isinstance(result.get('collectors'), dict)
                or set(result['collectors']) != {'threads', 'schedules', 'opencode'}
                or any(item.get('state') != 'polled' for item in result['collectors'].values())
                or result['collectors']['schedules'].get('diagnostics') !=
                   {'schedules': 'read_ok', 'research': 'read_ok'}):
            raise RuntimeError('Notifier baseline was not confirmed')
        for target, rel in targets.items():
            if digest(Path(target)) != reviewed[rel]:
                raise RuntimeError('Installed source differs from review')
        if protected_snapshot() != protected:
            raise RuntimeError('Protected finance, schedule specifications or profiles changed')
        report = {'installed': True, 'baseline_confirmed': True, 'gateway_restarted': True,
                  'opencode_restarted': False, 'engine_untouched': True,
                  'telegram_briefly_paused': telegram_paused, 'telegram_state_preserved': telegram,
                  'financial_operations_unchanged': True, 'schedule_specs_unchanged': True,
                  'engine_selection_unchanged': True, 'model_profiles_unchanged': True,
                  'watchdog_timer_preserved': states[WATCHDOG_TIMER],
                  'notifier_timer': 'enabled_active', 'rollback': str(before),
                  'sha256': {target: digest(Path(target)) for target in targets}}
        if telegram_paused:
            restore_telegram()
            telegram_paused = False
        if unit_state('is-enabled', TELEGRAM) != telegram['enabled']:
            raise RuntimeError('Telegram enablement changed unexpectedly')
        restore_timer(WATCHDOG_TIMER, states[WATCHDOG_TIMER])
        run(['systemctl', 'enable', TIMER])
        run(['systemctl', 'start', TIMER])
        (RELEASE / 'installation.json').write_text(json.dumps(report, indent=2))
        return report
    except BaseException as original:
        errors = []

        def attempt(operation):
            try:
                operation()
            except Exception as error:
                errors.append(type(error).__name__)

        if changed:
            # A final systemctl start may have succeeded before returning an
            # error. Stop timers, then wait for their workers instead of killing
            # a delivery or overwriting the running privileged watchdog.
            for unit in (TIMER, WATCHDOG_TIMER):
                attempt(lambda unit=unit: run(['systemctl', 'stop', unit]))
            deadline = time.monotonic() + 115
            while any(unit_state('is-active', unit) not in ('inactive', 'unknown', 'failed')
                      for unit in (SERVICE, WATCHDOG_SERVICE)):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Rollback deferred: active worker; reviewed source preserved') from original
                time.sleep(1)
            if errors:
                raise RuntimeError('Rollback deferred: timer stop failed; reviewed source preserved') from original
            # Activation/reporting can fail after the forward path restored the
            # transport. Quiesce it again before any rollback gateway restart.
            if gateway_restarted and unit_state('is-active', TELEGRAM) == 'active':
                telegram_paused = True
                pause_telegram()
            attempt(lambda: run(['systemctl', 'disable', TIMER]))
            for target in targets:
                if target in attributes:
                    index, uid, gid, mode = attributes[target]
                    attempt(lambda target=target, index=index, uid=uid, gid=gid, mode=mode:
                            replace(before / str(index), Path(target), uid, gid, mode))
                else:
                    attempt(lambda target=target: Path(target).unlink(missing_ok=True))
            attempt(lambda: run(['systemctl', 'daemon-reload']))
            if gateway_restarted:
                # If an owner turn started meanwhile, leave it running and flag
                # the pending gateway reload instead of interrupting that turn.
                def reload_gateway():
                    restart_gateway()
                attempt(reload_gateway)
            for unit in (WATCHDOG_TIMER, TIMER):
                attempt(lambda unit=unit: restore_timer(unit, states[unit],
                                                       states[unit]['enabled'] != 'not-found'))
            (RELEASE / 'installation.json').unlink(missing_ok=True)
        else:
            for unit in stopped:
                attempt(lambda unit=unit: run(['systemctl', 'start', unit]))
        if telegram_paused:
            attempt(restore_telegram)
            telegram_paused = False
        if errors:
            raise RuntimeError('Installation failed; rollback needs review: ' + ','.join(errors)) from original
        raise
    finally:
        # Even a deferred rollback must not strand an intentionally paused
        # transport. Gateway/source restoration is attempted first above.
        if telegram_paused:
            restore_telegram()


def main():
    if os.name != 'posix' or os.geteuid() != 0 or Path.cwd() != ROOT:
        raise RuntimeError('Run as root from /opt/iva')
    import fcntl
    import pwd
    lock_path = ROOT / 'data/application-deploy.lock'
    safe_path(lock_path)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = install(pwd.getpwnam('iva'))
    print(json.dumps({key: value for key, value in report.items() if key != 'sha256'}))


if __name__ == '__main__':
    main()
