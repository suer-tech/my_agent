"""Install the narrow provider-scope recovery without changing engine selection."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import sqlite3
import subprocess
import tempfile

ROOT = Path('/opt/iva')
RELEASE = ROOT / 'data/opencode-repair-20261025'
FILES = ['local_finance/opencode_service.py', 'tests/test_opencode_service.py',
         'deploy/opencode_health.py', 'tests/test_opencode_health.py',
         'deploy/verify_opencode_recovery.py', 'tests/test_verify_opencode_recovery.py', 'deploy/install_opencode_recovery.py',
         'deploy/systemd/iva-opencode-health.service', 'deploy/systemd/iva-opencode-health.timer']
EXTERNAL = {'/usr/local/lib/iva/opencode_health.py': 'deploy/opencode_health.py',
            '/etc/systemd/system/iva-opencode-health.service': 'deploy/systemd/iva-opencode-health.service',
            '/etc/systemd/system/iva-opencode-health.timer': 'deploy/systemd/iva-opencode-health.timer'}


def run(*args, **kw):
    return subprocess.run(list(args), check=True, **kw)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def quiet():
    with sqlite3.connect('file:/opt/iva/data/local-finance.sqlite3?mode=ro', uri=True) as db:
        if db.execute("SELECT count(*) FROM local_updates WHERE state='processing'").fetchone()[0]:
            return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3); client.connect('/run/iva-browser/control.sock')
        client.sendall(b'{"action":"research-status"}\n'); data = b''
        while b'\n' not in data:
            part = client.recv(65536)
            if not part or len(data) + len(part) > 262144:
                raise RuntimeError('Coordinator response unavailable')
            data += part
    result = json.loads(data.split(b'\n')[0])
    return result.get('ok') is True and result.get('status') == 'idle' and not result.get('active')


def snapshot():
    with sqlite3.connect('file:/opt/iva/data/local-finance.sqlite3?mode=ro', uri=True) as db:
        return {key: hashlib.sha256(json.dumps(db.execute(sql).fetchall()).encode()).hexdigest()
                for key, sql in {
                    'operations': 'SELECT * FROM operations ORDER BY id',
                    'schedule_specs': 'SELECT id,spec FROM scheduled_jobs ORDER BY id',
                    'selected_engine': "SELECT key,value FROM local_meta WHERE key='primary_engine'",
                }.items()}


def replace(source, target, uid, gid, mode):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + target.name + '.recovery-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(source.read_bytes()); out.flush(); os.fsync(out.fileno())
        os.chown(temporary, uid, gid); temporary.chmod(mode); os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    if os.geteuid() != 0 or Path.cwd() != ROOT:
        raise RuntimeError('Run as root from /opt/iva')
    with (ROOT / 'data/application-deploy.lock').open('a') as deploy_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not quiet():
            raise RuntimeError('Owner or research work is active; defer installation')
        expected = json.loads((RELEASE / 'expected-base.json').read_text())
        mapping = {str(ROOT / rel): rel for rel in FILES} | EXTERNAL
        for target, rel in mapping.items():
            if digest(Path(target)) != expected[target] or not (RELEASE / 'source' / rel).is_file():
                raise RuntimeError('Source changed or missing: ' + target)
        backup = RELEASE / 'before'; backup.mkdir(mode=0o700)
        attrs = {}
        for index, target in enumerate(mapping):
            path = Path(target)
            if path.exists():
                st = path.stat(); attrs[target] = (index, st.st_uid, st.st_gid, st.st_mode & 0o777)
                shutil.copy2(path, backup / str(index))
        original = snapshot()
        profiles = {str(ROOT / rel): digest(ROOT / rel) for rel in
                    ('data/iva-agents.json', 'data/opencode-primary.json')}
        account = pwd.getpwnam('iva')
        invocation = ROOT / 'data/opencode-runtime/inference.lock'
        if not invocation.exists():
            fd = os.open(invocation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o660)
            os.close(fd); os.chown(invocation, account.pw_uid, account.pw_gid); invocation.chmod(0o660)
        if invocation.is_symlink() or not invocation.is_file() or invocation.stat().st_uid != account.pw_uid:
            raise RuntimeError('Unexpected invocation lock')
        changed = False
        try:
            run('systemctl', 'stop', 'iva@telegram', timeout=40)
            if not quiet():
                raise RuntimeError('Work started before stop; defer installation')
            with invocation.open('rb') as invocation_lock:
                fcntl.flock(invocation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                changed = True
                for target, rel in mapping.items():
                    external = target in EXTERNAL
                    replace(RELEASE / 'source' / rel, Path(target), 0 if external else account.pw_uid,
                            0 if external else account.pw_gid, 0o644)
                run('systemd-analyze', 'verify', '/etc/systemd/system/iva-opencode-health.service',
                    '/etc/systemd/system/iva-opencode-health.timer', capture_output=True, timeout=20)
                with (RELEASE / 'tests.log').open('wb') as output:
                    for pattern in ('test_opencode_service.py', 'test_opencode_health.py', 'test_runtime_health.py', 'test_verify_opencode_recovery.py'):
                        run('runuser', '-u', 'iva', '--', str(ROOT / '.venv/bin/python'), '-m', 'unittest',
                            'discover', '-s', 'tests', '-p', pattern, '-v', cwd=ROOT,
                            stdout=output, stderr=subprocess.STDOUT, timeout=90)
                if snapshot() != original or any(digest(Path(p)) != h for p, h in profiles.items()):
                    raise RuntimeError('Protected live settings changed during installation')
            run('systemctl', 'daemon-reload', timeout=20)
            run('systemctl', 'start', 'iva@telegram', timeout=30)
            run('systemctl', 'enable', '--now', 'iva-opencode-health.timer', capture_output=True, timeout=20)
            run('systemctl', 'start', 'iva-opencode-health.service', timeout=55)
            report = {'installed': True, 'financial_operations_unchanged': True, 'schedule_specs_unchanged': True,
                      'engine_selection_unchanged': True, 'model_profiles_unchanged': True,
                      'runtime_sha256': {target: digest(Path(target)) for target in mapping}}
            (RELEASE / 'installation.json').write_text(json.dumps(report, indent=2))
            print(json.dumps({k: v for k, v in report.items() if k != 'runtime_sha256'}), flush=True)
        except BaseException:
            if changed:
                subprocess.run(['systemctl', 'disable', '--now', 'iva-opencode-health.timer'], capture_output=True)
                for target in mapping:
                    if target in attrs:
                        index, uid, gid, mode = attrs[target]
                        replace(backup / str(index), Path(target), uid, gid, mode)
                    else:
                        Path(target).unlink(missing_ok=True)
                subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
            subprocess.run(['systemctl', 'start', 'iva@telegram'], capture_output=True)
            raise


if __name__ == '__main__':
    main()
