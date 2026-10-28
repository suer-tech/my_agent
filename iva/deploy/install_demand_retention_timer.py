"""Install retention units after the code upgrade; deliberately do not run them.

Run as root from /opt/iva, passing the completed demand-release-* directory.
The caller may start the timer/service only after this process releases the
application deployment lock. Enabling a timer does not activate its service.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path('/opt/iva')
UNIT_ROOT = Path('/etc/systemd/system')
TIMER = 'iva-demand-retention.timer'
SERVICE = 'iva-demand-retention.service'
UNITS = (SERVICE, TIMER)
REQUIRED_CODE = (
    'iva_integration/demand_retention.py', 'iva_integration/demand_schedule_retention.py',
    'iva_integration/demand_storage.py', 'deploy/backup.py', 'deploy/verify_demand_retention.py',
    'tests/test_demand_retention.py', 'tests/test_demand_schedule_retention.py',
    'tests/test_demand_storage.py', 'tests/test_backup_capacity.py',
    'deploy/install_demand_retention_timer.py', 'tests/test_demand_retention_install.py',
    'deploy/systemd/' + SERVICE, 'deploy/systemd/' + TIMER,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)


def state(verb, unit):
    result = subprocess.run(['systemctl', verb, unit], capture_output=True, text=True, timeout=10)
    value = result.stdout.strip()
    if not value:
        loaded = subprocess.run(['systemctl', 'show', '--property=LoadState', '--value', unit],
                                capture_output=True, text=True, timeout=10)
        if loaded.returncode == 0 and loaded.stdout.strip() == 'not-found':
            return 'not-found' if verb == 'is-enabled' else 'unknown'
        raise RuntimeError('Cannot read unit state: ' + unit)
    return value


def verify_code(release):
    report = json.loads((release / 'installation.json').read_text())
    if report.get('installed') is not True:
        raise RuntimeError('A successful code deployment is required before enabling retention')
    hashes = report.get('sha256', {})
    for rel in REQUIRED_CODE:
        path = ROOT / rel
        if not path.is_file() or not (release / rel).is_file() or hashes.get(rel) != digest(path) or digest(path) != digest(release / rel):
            raise RuntimeError('Deployed retention source differs from the successful release: ' + rel)


def replace(source, target, uid, gid, mode):
    descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.retention-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(source.read_bytes()); stream.flush(); os.fsync(stream.fileno())
        os.chown(temporary, uid, gid); temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install_units(release):
    """Caller holds application-deploy.lock and has verified the idle boundary."""
    verify_code(release)
    enabled = state('is-enabled', TIMER)
    active = state('is-active', TIMER)
    if enabled not in ('enabled', 'disabled', 'not-found') or active not in ('active', 'inactive', 'failed', 'unknown'):
        raise RuntimeError('Unexpected existing timer state; preserve it for review')
    if state('is-active', SERVICE) not in ('inactive', 'failed', 'unknown'):
        raise RuntimeError('Retention service is active; wait for it to finish')
    before = release / 'retention-units-before'
    before.mkdir(mode=0o700)
    attributes = {}
    for name in UNITS:
        path = UNIT_ROOT / name
        if path.is_symlink():
            raise RuntimeError('Refusing to replace a symlink unit: ' + name)
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
                raise RuntimeError('Existing unit must be a root-owned regular file: ' + name)
            attributes[name] = [info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
            shutil.copy2(path, before / name)
    (before / 'state.json').write_text(json.dumps({'enabled': enabled, 'active': active, 'attributes': attributes}))
    changed = False
    stopped = False
    try:
        if active == 'active':
            stopped = True
            run(['systemctl', 'stop', TIMER])
        # A timer may have fired just before stop completed. Never replace code
        # or units while a retention invocation is still executing.
        if state('is-active', SERVICE) not in ('inactive', 'failed', 'unknown'):
            raise RuntimeError('Retention started before the timer stopped; retry after completion')
        changed = True
        for name in UNITS:
            replace(ROOT / 'deploy/systemd' / name, UNIT_ROOT / name, 0, 0, 0o644)
        run(['systemd-analyze', 'verify', *[str(UNIT_ROOT / name) for name in UNITS]])
        run(['systemctl', 'daemon-reload'])
        # No --now: the deploy lock remains held until main() returns. Starting
        # the persistent timer here could run its service inside this lock.
        run(['systemctl', 'enable', TIMER])
        report = {'installed': True, 'timer_enabled': True, 'timer_started': False,
                  'service_started': False, 'rollback': str(before),
                  'sha256': {name: digest(UNIT_ROOT / name) for name in UNITS}}
        (release / 'retention-units-installation.json').write_text(json.dumps(report, indent=2))
        return report
    except BaseException as original:
        errors = []

        def attempt(operation):
            try:
                operation()
            except Exception as error:
                errors.append(type(error).__name__)

        if changed:
            attempt(lambda: run(['systemctl', 'disable', '--now', TIMER]))
            for name in UNITS:
                if name in attributes:
                    attempt(lambda name=name: replace(before / name, UNIT_ROOT / name, *attributes[name]))
                else:
                    attempt(lambda name=name: (UNIT_ROOT / name).unlink(missing_ok=True))
            attempt(lambda: run(['systemctl', 'daemon-reload']))
            if enabled == 'enabled':
                attempt(lambda: run(['systemctl', 'enable', TIMER]))
            elif enabled == 'disabled':
                attempt(lambda: run(['systemctl', 'disable', TIMER]))
        if stopped:
            attempt(lambda: run(['systemctl', 'start', TIMER]))
        if errors:
            raise RuntimeError('Retention unit installation failed; rollback needs review: ' + ','.join(errors)) from original
        raise


def main():
    if os.name != 'posix' or os.geteuid() != 0 or Path.cwd() != ROOT:
        raise RuntimeError('Run as root from /opt/iva')
    release = Path(sys.argv[1]).resolve()
    if not release.is_relative_to(ROOT / 'data') or not release.name.startswith('demand-release-'):
        raise ValueError('Invalid release directory')
    import fcntl
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from deploy.upgrade_demand_research import idle
    with (ROOT / 'data/application-deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not idle():
            raise RuntimeError('Owner or research work is active; defer unit installation')
        report = install_units(release)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
