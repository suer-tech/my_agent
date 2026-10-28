"""Guarded OpenCode primary interpreter release, preserving neighboring edits and state."""
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


def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def active():
    session=ROOT/'data/iva-telegram/session.json'
    if session.exists() and json.loads(session.read_text()).get('active_source'):return True
    with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:
        return bool(db.execute("SELECT id FROM local_updates WHERE state='processing'").fetchall())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release',type=Path,required=True);args=parser.parse_args()
    release=args.release.resolve()
    if not release.is_relative_to(ROOT/'data') or not release.name.startswith('opencode-release-'):raise ValueError('Unexpected release')
    with (ROOT/'data/application-deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        expected=json.loads((release/'expected.json').read_text())
        assert json.loads((release/'staged-verification.json').read_text())['passed']
        assert json.loads((release/'routing-verification.json').read_text())['passed']
        assert json.loads((release/'permission-verification.json').read_text())['passed']
        with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:
            old_mode=db.execute("SELECT value FROM local_meta WHERE key='primary_engine'").fetchone()
        profiles=digest(ROOT/'data/iva-agents.json')
        for rel,h in expected.items():
            if digest(ROOT/rel)!=h:raise RuntimeError('Concurrent source change: '+rel)
        if active():raise RuntimeError('Owner turn active; retry when idle')
        backup=release/'backup'
        if backup.exists():raise RuntimeError('Prior installation exists; inspect before retry')
        files=[str(p.relative_to(release/'source')) for p in (release/'source').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        baseline={rel:digest(ROOT/rel) for rel in files}
        backup.mkdir(mode=0o700)
        for rel,h in baseline.items():
            if h is not None:
                target=backup/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,target)
        with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db,sqlite3.connect(backup/'before.sqlite3') as target:db.backup(target)
        (backup/'before.sqlite3').chmod(0o600)
        stopped=False;changed=False
        try:
            run(['systemctl','stop','iva@telegram']);stopped=True
            if active():raise RuntimeError('Owner turn started; defer upgrade')
            run(['systemctl','stop','iva@gateway'])
            for rel,h in baseline.items():
                if digest(ROOT/rel)!=h:raise RuntimeError('Concurrent source change: '+rel)
            changed=True
            for rel in files:
                target=ROOT/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(release/'source'/rel,target);shutil.chown(target,user='iva',group='iva')
            with (release/'installed-tests.log').open('wb') as out:
                run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-m','unittest','discover','-s','tests','-q'],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=120)
            with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:
                db.execute("INSERT OR REPLACE INTO local_meta VALUES('primary_engine','opencode')")
                db.execute("DELETE FROM local_meta WHERE key='opencode_retry_after'")
            assert digest(ROOT/'data/iva-agents.json')==profiles
            run(['systemctl','start','iva@gateway','iva@telegram'])
            config=json.loads((ROOT/'data/iva-pilot/control.json').read_text());deadline=time.monotonic()+30
            while True:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:'+str(config['gateway_port'])+'/health',timeout=2) as response:
                        if response.status==200:break
                except OSError:pass
                if time.monotonic()>deadline:raise RuntimeError('Gateway did not become healthy')
                time.sleep(1)
            services=run(['systemctl','is-active','iva@gateway','iva@telegram','iva@engine','iva@research','iva-opencode'],capture_output=True,text=True).stdout.splitlines()
            result={'installed':True,'services':services,'backup':str(backup),'engine_and_research_not_restarted':True,'primary_engine':'opencode','specialist_profiles_unchanged':True}
            (release/'installation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result))
        except BaseException:
            if changed:
                for rel,h in baseline.items():
                    target=ROOT/rel
                    if h is None:target.unlink(missing_ok=True)
                    else:shutil.copy2(backup/rel,target);shutil.chown(target,user='iva',group='iva')
            with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:
                if old_mode:db.execute("INSERT OR REPLACE INTO local_meta VALUES('primary_engine',?)",old_mode)
                else:db.execute("DELETE FROM local_meta WHERE key='primary_engine'")
            if stopped:subprocess.run(['systemctl','start','iva@gateway','iva@telegram'],capture_output=True)
            raise


if __name__=='__main__':main()
