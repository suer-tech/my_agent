"""Checkpoint-preserving upgrade of the existing tender specialist on its VPS."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT=Path('/opt/iva');APP=ROOT/'data/iva-pilot/app'
SERVICES=('iva@gateway','iva@engine','iva@telegram','iva@research')

def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
def active_turn():return bool(json.loads((ROOT/'data/iva-telegram/session.json').read_text()).get('active_source'))
def operations():
    with sqlite3.connect(f'file:{ROOT}/data/local-finance.sqlite3?mode=ro',uri=True) as db:
        return hashlib.sha256(json.dumps(db.execute('SELECT * FROM operations ORDER BY 1').fetchall(),ensure_ascii=False).encode()).hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('release',type=Path);p.add_argument('--live-check',action='store_true');a=p.parse_args()
    release=a.release.resolve();source=release/'source';backup=release/'backup'
    if not release.is_relative_to(ROOT/'data') or not release.name.startswith('tender-skills-release-'):raise ValueError('Unexpected release location')
    manifest=json.loads((release/'baseline.json').read_text());files=manifest['files'];expected=manifest['expected']
    for rel in list(expected)+files:
        if not (ROOT/rel).resolve().is_relative_to(ROOT):raise ValueError('Path leaves application root')
    if backup.exists():raise RuntimeError('Backup already exists; use a new release')
    for rel,sha in expected.items():
        if digest(ROOT/rel)!=sha:raise RuntimeError('Concurrent source change: '+rel)
    for rel in files:
        if digest(source/rel)!=manifest['payload_sha256'][rel]:raise RuntimeError('Payload checksum mismatch: '+rel)
    expected_space=(ROOT/'data/tender-agent.sqlite3').stat().st_size+2*sum(p.stat().st_size for p in (APP/'.output').rglob('*') if p.is_file())+250*1024*1024
    if shutil.disk_usage(release).free<expected_space:raise RuntimeError('Not enough space for the backup, build and worker reserve')
    if active_turn():raise RuntimeError('An owner turn is active; wait before deployment')
    backup.mkdir(mode=0o700)
    for rel in expected:
        path=ROOT/rel
        if path.is_file():
            dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    shutil.copytree(APP/'.output',backup/'compiled-output')
    changed=False;stopped=False
    try:
        run(['systemctl','stop','iva@telegram']);stopped=True
        if active_turn():raise RuntimeError('A new owner turn started; abort without changing code')
        run(['systemctl','stop','iva@research','iva@engine','iva@gateway'])
        before=operations()
        with sqlite3.connect(f'file:{ROOT}/data/tender-agent.sqlite3?mode=ro',uri=True) as db,sqlite3.connect(backup/'tender-agent.sqlite3') as dest:db.backup(dest)
        changed=True
        for rel in files:
            path=ROOT/rel;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/rel,path);shutil.chown(path,user='iva',group='iva')
        for name in ('tender-search','tender-history'):
            for folder in (APP/'agent/skills'/name,APP/'data/custom/agent/skills'/name):
                folder.mkdir(parents=True,exist_ok=True)
                shutil.copy2(ROOT/'iva_integration/skills'/name/'SKILL.md',folder/'SKILL.md')
                shutil.chown(folder,user='iva',group='iva');shutil.chown(folder/'SKILL.md',user='iva',group='iva')
        shutil.copy2(ROOT/'iva_integration/tender_agent.ts',APP/'agent/tools/tender_agent.ts')
        tree=ast.parse((ROOT/'iva_integration/gateway.py').read_text())
        rules=[n.value.value for n in ast.walk(tree) if isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='instruction' and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str) and 'JSON ROUTER' not in n.value.value]
        instructions=APP/'agent/instructions/99-pilot.md'
        instructions.write_text('# Рабочая Ива\nТы личный ассистент владельца. Отвечай по-русски. Подтверждай выполнение по результату инструмента.\n'+''.join(rules))
        for path in (instructions,APP/'agent/tools/tender_agent.ts'):shutil.chown(path,user='iva',group='iva')
        with (release/'tests.log').open('wb') as out:
            run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-m','unittest','discover','-s','tests','-p','test_tender*.py','-q'],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=120)
        with (release/'build.log').open('wb') as out:
            run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),str(ROOT/'iva_integration/run_pilot.py'),'build'],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=240)
        run(['systemctl','start','iva@gateway','iva@engine'])
        config=json.loads((ROOT/'data/iva-pilot/control.json').read_text())
        for port,route in [(config['gateway_port'],'/health'),(config['iva_port'],'/')]:
            deadline=time.monotonic()+60
            while True:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}{route}',timeout=3) as response:
                        if response.status==200:break
                except OSError:pass
                if time.monotonic()>deadline:raise RuntimeError('Service health did not recover')
                time.sleep(1)
        verify=['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),str(ROOT/'deploy/verify_tender_skills.py'),'--release',str(release)]
        if a.live_check:verify.append('--live')
        with (release/'verification.log').open('wb') as out:run(verify,cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=400)
        if operations()!=before:raise RuntimeError('Financial operations fingerprint changed')
        run(['systemctl','start','iva@telegram','iva@research'])
        states=run(['systemctl','is-active',*SERVICES],capture_output=True,text=True).stdout.splitlines()
        result={'installed':True,'release':str(release),'services':states,'financial_operations_unchanged':True,'backup':str(backup),'files':files}
        (release/'installation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));os.chmod(release/'installation.json',0o600)
        print(json.dumps(result,ensure_ascii=False))
    except Exception:
        if changed:
            subprocess.run(['systemctl','stop',*SERVICES],capture_output=True)
            for rel,sha in expected.items():
                path=ROOT/rel;saved=backup/rel
                if sha is None:path.unlink(missing_ok=True)
                elif saved.is_file():path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(saved,path);shutil.chown(path,user='iva',group='iva')
            # Only the named generated output under this app is replaced.
            output=APP/'.output'
            if output.exists():shutil.rmtree(output)
            shutil.copytree(backup/'compiled-output',output)
            for path in [output,*output.rglob('*')]:shutil.chown(path,user='iva',group='iva')
        if stopped:subprocess.run(['systemctl','start',*SERVICES],capture_output=True)
        raise

if __name__=='__main__':main()
