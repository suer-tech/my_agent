"""Add to the latest deployment, retaining adjacent-session tender changes."""
import argparse
import ast
import fcntl
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

ROOT=Path(__file__).resolve().parents[1];APP=ROOT/'data/iva-pilot/app'
sys.path.insert(0,str(ROOT))
from deploy.personal_skills_integration import SHARED,transform
from iva_integration.personal_skills import Store


def run(args,**kw):return subprocess.run(args,check=True,**kw)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def active():
    p=ROOT/'data/iva-telegram/session.json'
    if p.exists() and json.loads(p.read_text()).get('active_source'):return True
    with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:
        return bool(db.execute("SELECT count(*) FROM local_updates WHERE state='processing'").fetchone()[0])


def busy():
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try:args=p.read_bytes().decode().split('\0')
        except (OSError,UnicodeError):continue
        if any(Path(a).name=='install_tender_skills.py' or a.endswith('scripts/build.ts') or (a.endswith('/eve.js') and 'build' in args) for a in args):return True
    return False


def financial():
    with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:
        return hashlib.sha256(json.dumps(db.execute('SELECT * FROM operations ORDER BY id').fetchall()).encode()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True);args=p.parse_args()
    release=args.release.resolve()
    if ROOT.as_posix()!='/opt/iva' or not release.is_relative_to(ROOT/'data') or not release.name.startswith('personal-skills-release-'):raise ValueError('Canonical VPS release only')
    lock=(ROOT/'data/application-deploy.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if busy():raise RuntimeError('Adjacent deployment/build is running; retry after it finishes')
    if active():raise RuntimeError('Owner turn is active; wait before deployment')
    backup=release/'backup'
    if backup.exists():raise RuntimeError('Backup exists; inspect prior attempt instead of overwriting')
    if shutil.disk_usage(ROOT).free<250*1024**2:raise RuntimeError('Not enough free space for build and backup')
    backup.mkdir(mode=0o700)
    paths=list(SHARED)+['data/iva-pilot/app/agent/instructions/99-pilot.md','data/iva-pilot/app/agent/tools/personal_skills.ts','data/iva-pilot/app/agent/skills/create-personal-skill/SKILL.md','data/iva-pilot/app/data/custom/agent/skills/create-personal-skill/SKILL.md','data/iva-pilot/app/data/custom/agent/skills/personal-movie-discovery/SKILL.md']
    expected={rel:digest(ROOT/rel) for rel in paths}
    for rel in paths:
        if expected[rel] is not None:
            dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,dest)
    shutil.copytree(APP/'.output',backup/'compiled-output')
    transformed={rel:transform(rel,(ROOT/rel).read_text()) for rel in SHARED}
    (release/'baseline.json').write_text(json.dumps(expected,indent=2))
    stopped=False;changed=False
    try:
        run(['systemctl','stop','iva@telegram']);stopped=True
        if active():raise RuntimeError('Owner turn started before stop; do not change runtime')
        if busy():raise RuntimeError('Another build started; do not change runtime')
        run(['systemctl','stop','iva@engine','iva@gateway'])
        before=financial()
        for rel,sha in expected.items():
            if digest(ROOT/rel)!=sha:raise RuntimeError('Concurrent source modification: '+rel)
        changed=True
        for rel,content in transformed.items():
            target=ROOT/rel;tmp=target.with_name(target.name+'.personal-new');tmp.write_text(content);shutil.chown(tmp,user='iva',group='iva');os.replace(tmp,target)
        for origin,target in [(ROOT/'iva_integration/personal_skills.ts',APP/'agent/tools/personal_skills.ts'),(ROOT/'iva_integration/skills/create-personal-skill/SKILL.md',APP/'agent/skills/create-personal-skill/SKILL.md'),(ROOT/'iva_integration/skills/create-personal-skill/SKILL.md',APP/'data/custom/agent/skills/create-personal-skill/SKILL.md')]:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(origin,target);shutil.chown(target,user='iva',group='iva');shutil.chown(target.parent,user='iva',group='iva')
        tree=ast.parse((ROOT/'iva_integration/gateway.py').read_text())
        rules=[n.value.value for n in ast.walk(tree) if isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='instruction' and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str) and 'JSON ROUTER' not in n.value.value]
        instructions=APP/'agent/instructions/99-pilot.md'
        instructions.write_text('# Рабочая Ива\nТы личный ассистент владельца. Отвечай по-русски. Используй нативные инструменты. Подтверждай выполнение по результату инструмента.\n'+''.join(rules));shutil.chown(instructions,user='iva',group='iva')
        # A usable starter procedure with no fabricated personal movie history.
        s=Store()
        try:
            if not s.rows("SELECT name FROM skills WHERE name='personal-movie-discovery'"):
                original=(ROOT/'iva_integration/skills/personal-movie-discovery/SKILL.md').read_text()
                evidence='добавь в иву функционал: чтобы она умела добавлять себе нужные навыки'
                s.execute({'action':'save','name':'personal-movie-discovery','title':'Подбор фильмов по моим вкусам','description':original.split('description: ',1)[1].splitlines()[0],'instructions':original.split('---',2)[2].strip(),'expected_revision':0,'evidence':evidence},'maintenance:owner:personal-skills:2026-10-21',evidence)
        finally:s.close()
        for folder in (ROOT/'data/personal-skills',APP/'data/custom/agent/skills/personal-movie-discovery'):
            for path in [folder,*folder.rglob('*')]:shutil.chown(path,user='iva',group='iva')
        with (release/'tests.log').open('wb') as out:
            run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-m','unittest','discover','-s','tests','-q'],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=120)
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
        if before!=financial():raise RuntimeError('Financial operations changed during deployment')
        run(['systemctl','start','iva@telegram'])
        states=run(['systemctl','is-active','iva@gateway','iva@engine','iva@telegram','iva@research'],capture_output=True,text=True).stdout.splitlines()
        report={'installed':True,'financial_operations_unchanged':True,'services':states,'backup':str(backup),'tender_code_replaced':False}
        (release/'installation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
    except BaseException:
        if changed:
            subprocess.run(['systemctl','stop','iva@telegram','iva@engine','iva@gateway'],capture_output=True)
            for rel,sha in expected.items():
                target=ROOT/rel;saved=backup/rel
                if sha is None:target.unlink(missing_ok=True)
                else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(saved,target);shutil.chown(target,user='iva',group='iva')
            output=APP/'.output'
            if output.resolve()!=Path('/opt/iva/data/iva-pilot/app/.output'):raise RuntimeError('Unexpected compiled output path')
            shutil.rmtree(output);shutil.copytree(backup/'compiled-output',output)
            for p in [output,*output.rglob('*')]:shutil.chown(p,user='iva',group='iva')
        if stopped:subprocess.run(['systemctl','start','iva@gateway','iva@engine','iva@telegram'],capture_output=True)
        raise
    finally:lock.close()


if __name__=='__main__':main()
