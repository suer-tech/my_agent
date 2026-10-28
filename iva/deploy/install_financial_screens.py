"""Hash-guarded Python-only upgrade; native engine and tender worker stay up."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request

ROOT=Path('/opt/iva')


def run(args,**kw):return subprocess.run(args,check=True,**kw)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def active():
    p=ROOT/'data/iva-telegram/session.json'
    if p.exists() and json.loads(p.read_text()).get('active_source'):return True
    with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as c:return bool(c.execute("SELECT id FROM local_updates WHERE state='processing'").fetchall())


def main():
    p=argparse.ArgumentParser();p.add_argument('--release',required=True,type=Path);a=p.parse_args();r=a.release.resolve()
    if not r.is_relative_to(ROOT/'data') or not r.name.startswith('screen-release-'):raise ValueError('Unexpected release')
    with (ROOT/'data/application-deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        expected=json.loads((r/'expected.json').read_text());report=json.loads((r/'model-verification.json').read_text())
        assert report['passed'] and report['cached_repeat'],'Real model verification required'
        for p,h in expected.items():
            if digest(ROOT/p)!=h:raise RuntimeError('Concurrent source edit: '+p)
        if active():raise RuntimeError('Owner turn active; retry after it finishes')
        backup=r/'backup'
        if backup.exists():raise RuntimeError('Backup exists; inspect prior installation')
        backup.mkdir(mode=0o700)
        files=[str(p.relative_to(r/'source')) for p in (r/'source').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        baseline={rel:digest(ROOT/rel) for rel in files}
        for rel,h in baseline.items():
            if h is not None:
                target=backup/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,target)
        with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as c,sqlite3.connect(backup/'before.sqlite3') as target:c.backup(target)
        (backup/'before.sqlite3').chmod(0o600)
        stopped=False;changed=False
        try:
            run(['systemctl','stop','iva@telegram']);stopped=True
            if active():raise RuntimeError('Owner turn started; defer upgrade')
            run(['systemctl','stop','iva@gateway'])
            for rel,h in baseline.items():
                if digest(ROOT/rel)!=h:raise RuntimeError('Concurrent change: '+rel)
            changed=True
            for rel in files:
                target=ROOT/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(r/'source'/rel,target);shutil.chown(target,user='iva',group='iva')
            with (r/'tests.log').open('wb') as out:
                run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-m','unittest','discover','-s','tests','-q'],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=120)
            for flag in ([],['--apply']):
                with (r/('repair-live.log' if flag else 'repair-dry.log')).open('wb') as out:
                    run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),str(ROOT/'deploy/repair_screen_extraction.py'),'--release',str(r),*flag],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=60)
            run(['systemctl','start','iva@gateway','iva@telegram'])
            cfg=json.loads((ROOT/'data/iva-pilot/control.json').read_text());deadline=time.monotonic()+30
            while True:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:'+str(cfg['gateway_port'])+'/health',timeout=2) as response:
                        if response.status==200:break
                except OSError:pass
                if time.monotonic()>deadline:raise RuntimeError('Gateway health failed')
                time.sleep(1)
            states=run(['systemctl','is-active','iva@gateway','iva@telegram','iva@engine','iva@research'],capture_output=True,text=True).stdout.splitlines()
            result={'installed':True,'services':states,'source_repaired':True,'cash_and_debts_unchanged':True,'engine_and_research_not_restarted':True,'backup':str(backup)}
            (r/'installation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
        except BaseException:
            if changed:
                for rel,h in baseline.items():
                    target=ROOT/rel
                    if h is None:target.unlink(missing_ok=True)
                    else:shutil.copy2(backup/rel,target);shutil.chown(target,user='iva',group='iva')
            if stopped:subprocess.run(['systemctl','start','iva@gateway','iva@telegram'],capture_output=True)
            raise


if __name__=='__main__':main()
