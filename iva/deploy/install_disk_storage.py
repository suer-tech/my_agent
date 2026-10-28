"""Install the reviewed disk tool on the live VPS, with rollback and health checks."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path('/opt/iva')
RELEASE = ROOT / 'data/disk-release-20261021'
SOURCE = RELEASE / 'source'
BACKUP = RELEASE / 'backup'
APP = ROOT / 'data/iva-pilot/app'
HELPER = Path('/usr/local/sbin/iva-disk-storage')
SUDO = Path('/etc/sudoers.d/iva-disk-storage')
EXTRAS = ['data/iva-pilot/app/agent/tools/disk_storage.ts', 'data/iva-pilot/app/agent/instructions/99-pilot.md']


def run(args, **kwargs): return subprocess.run(args, check=True, **kwargs)


def finance_hash():
    with sqlite3.connect(f'file:{ROOT}/data/local-finance.sqlite3?mode=ro', uri=True) as db:
        return hashlib.sha256(json.dumps(db.execute('SELECT * FROM operations ORDER BY 1').fetchall(), ensure_ascii=False).encode()).hexdigest()


def health():
    config = json.loads((ROOT / 'data/iva-pilot/control.json').read_text())
    for key, path in [('gateway_port', '/health'), ('iva_port', '/')]:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{config[key]}{path}', timeout=3) as response:
                    if response.status == 200: break
            except OSError: pass
            if time.monotonic() > deadline: raise RuntimeError('Service health failed')
            time.sleep(1)


def main():
    if os.geteuid() != 0: raise PermissionError('Run as root')
    baseline = json.loads((RELEASE / 'baseline.json').read_text())
    for rel, expected in baseline.items():
        p = ROOT / rel
        actual = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        if actual != expected: raise RuntimeError('Concurrent change: ' + rel)
        if not (SOURCE / rel).is_file(): raise RuntimeError('Missing staged source: ' + rel)
    if HELPER.exists() or SUDO.exists(): raise RuntimeError('Existing privileged installation: review before replacement')
    if BACKUP.exists(): raise RuntimeError('Backup exists; never overwrite it')
    BACKUP.mkdir(mode=0o700)
    for rel in list(baseline) + EXTRAS:
        p = ROOT / rel
        if p.exists():
            dest = BACKUP / rel; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dest)
    shutil.copytree(APP / '.output', BACKUP / 'compiled-output')
    changed = False
    try:
        run(['systemctl', 'stop', 'iva@telegram'])
        if json.loads((ROOT / 'data/iva-telegram/session.json').read_text()).get('active_source'):
            raise RuntimeError('An owner turn did not finish')
        before = finance_hash()
        run(['systemctl', 'stop', 'iva@engine', 'iva@gateway'])
        changed = True
        for rel in baseline:
            p = ROOT / rel; p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / rel, p); shutil.chown(p, user='iva', group='iva')
        temp = HELPER.with_suffix('.new')
        shutil.copy2(ROOT / 'deploy/disk_storage_root.py', temp); os.chown(temp, 0, 0); temp.chmod(0o755); temp.replace(HELPER)
        sudo_temp = Path('/etc/sudoers.d/.iva-disk-storage.new')
        sudo_temp.write_text('iva ALL=(root) NOPASSWD: /usr/local/sbin/iva-disk-storage ""\n')
        os.chown(sudo_temp, 0, 0); sudo_temp.chmod(0o440)
        run(['/usr/sbin/visudo', '-cf', str(sudo_temp)], stdout=subprocess.DEVNULL)
        sudo_temp.replace(SUDO)
        probe = run(['runuser', '-u', 'iva', '--', '/usr/bin/sudo', '-n', str(HELPER)], input='{"action":"status"}', text=True, capture_output=True)
        if not json.loads(probe.stdout).get('ok'): raise RuntimeError('Installed helper failed')
        shutil.copy2(ROOT / 'iva_integration/disk_storage.ts', APP / 'agent/tools/disk_storage.ts')
        tree = ast.parse((ROOT / 'iva_integration/gateway.py').read_text())
        rules = [n.value.value for n in ast.walk(tree) if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)
                 and n.target.id == 'instruction' and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                 and 'JSON ROUTER' not in n.value.value]
        instructions = APP / 'agent/instructions/99-pilot.md'
        instructions.write_text('# Рабочая Ива\nТы личный ассистент владельца. Отвечай по-русски. Используй нативные инструменты. Подтверждай выполнение только по результату инструмента. Финансовые факты хранятся в реестре на VPS.\n' + ''.join(rules))
        for p in [instructions, APP / 'agent/tools/disk_storage.ts']: shutil.chown(p, user='iva', group='iva')
        with (RELEASE / 'build.log').open('wb') as log:
            run(['runuser', '-u', 'iva', '--', str(ROOT / '.venv/bin/python'), str(ROOT / 'iva_integration/run_pilot.py'), 'build'],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        if finance_hash() != before: raise RuntimeError('Financial operations unexpectedly changed during upgrade')
        run(['systemctl', 'start', 'iva@gateway', 'iva@engine']); health()
        run(['systemctl', 'start', 'iva@telegram', 'iva@memory'])
        result = {'installed': True, 'files': list(baseline), 'financial_operations_unchanged': True,
                  'research_worker_preserved': run(['systemctl', 'is-active', 'iva@research'], capture_output=True, text=True).stdout.strip() == 'active',
                  'backup': str(BACKUP)}
        (RELEASE / 'installation.json').write_text(json.dumps(result, indent=2)); print(json.dumps(result))
    except BaseException:
        if changed:
            run(['systemctl', 'stop', 'iva@engine', 'iva@gateway'])
            for rel in list(baseline) + EXTRAS:
                p = ROOT / rel; saved = BACKUP / rel
                if saved.exists(): shutil.copy2(saved, p); shutil.chown(p, user='iva', group='iva')
                else: p.unlink(missing_ok=True)
            if (APP / '.output').resolve() != APP / '.output': raise RuntimeError('Unsafe compiled-output path')
            shutil.rmtree(APP / '.output'); shutil.copytree(BACKUP / 'compiled-output', APP / '.output')
            for p in [APP / '.output', *(APP / '.output').rglob('*')]: shutil.chown(p, user='iva', group='iva')
            HELPER.unlink(missing_ok=True); SUDO.unlink(missing_ok=True)
            run(['systemctl', 'start', 'iva@gateway', 'iva@engine']); health()
        run(['systemctl', 'start', 'iva@telegram', 'iva@memory'])
        raise


if __name__ == '__main__': main()
