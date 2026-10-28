"""Install read-only mail support with rollback and no Telegram test sends."""
import ast,fcntl,hashlib,json,os,shutil,sqlite3,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];APP=ROOT/'data/iva-pilot/app'
sys.path.insert(0,str(ROOT))
from deploy.mail_integration import SHARED,transform,GUIDANCE

def run(args,**kw):return subprocess.run(args,check=True,**kw)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def active():
    p=ROOT/'data/iva-telegram/session.json'
    if p.exists() and json.loads(p.read_text()).get('active_source'):return True
    with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:return bool(db.execute("SELECT count(*) FROM local_updates WHERE state='processing'").fetchone()[0])
def busy():
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try:args=p.read_bytes().decode().split('\0')
        except (OSError,UnicodeError):continue
        if any(a.endswith('scripts/build.ts') or (a.endswith('/eve.js') and 'build' in args) for a in args):return True
    return False
def financial():
    with sqlite3.connect(ROOT/'data/local-finance.sqlite3') as db:return hashlib.sha256(json.dumps(db.execute('SELECT * FROM operations ORDER BY id').fetchall()).encode()).hexdigest()

def main():
    if ROOT!=Path('/opt/iva'):raise RuntimeError('VPS only')
    lock=(ROOT/'data/application-deploy.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if active() or busy():raise RuntimeError('Active owner turn/build; retry later without stopping it')
    if shutil.disk_usage(ROOT).free<250*1024**2:raise RuntimeError('Insufficient build space')
    release=ROOT/'data'/('mail-release-'+time.strftime('%Y%m%d-%H%M%S'));backup=release/'backup';backup.mkdir(parents=True,mode=0o700)
    paths=list(SHARED)+['data/iva-pilot/app/agent/instructions/99-pilot.md','data/iva-pilot/app/agent/tools/personal_mail.ts']
    expected={rel:digest(ROOT/rel) for rel in paths}
    transformed={rel:transform(rel,(ROOT/rel).read_text()) for rel in SHARED}
    for rel,sha in expected.items():
        if sha is not None:
            dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,dest)
    shutil.copytree(APP/'.output',backup/'compiled-output');(release/'baseline.json').write_text(json.dumps(expected,indent=2))
    changed=stopped=False;before=financial()
    try:
        run(['systemctl','stop','iva@telegram']);stopped=True
        if active() or busy():raise RuntimeError('Owner turn/build appeared; no runtime edits')
        run(['systemctl','stop','iva@engine','iva@gateway'])
        for rel,sha in expected.items():
            if digest(ROOT/rel)!=sha:raise RuntimeError('Concurrent modification: '+rel)
        changed=True
        for rel,text in transformed.items():
            p=ROOT/rel;tmp=p.with_name(p.name+'.mail-new');tmp.write_text(text);shutil.chown(tmp,user='iva',group='iva');os.replace(tmp,p)
        target=APP/'agent/tools/personal_mail.ts';shutil.copy2(ROOT/'iva_integration/personal_mail.ts',target);shutil.chown(target,user='iva',group='iva')
        instructions=APP/'agent/instructions/99-pilot.md';text=instructions.read_text();text=text.replace('Email, external Google Calendar and personal Telegram access are NOT connected.','External Google Calendar and personal Telegram access are NOT connected. Check personal_mail status for Yandex email.')
        if GUIDANCE not in text:text+='\n'+GUIDANCE+'\n'
        instructions.write_text(text);shutil.chown(instructions,user='iva',group='iva')
        with (release/'tests.log').open('wb') as out:run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-m','unittest','discover','-s','tests','-p','test_personal_mail.py','-v'],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=60)
        with (release/'build.log').open('wb') as out:run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),str(ROOT/'iva_integration/run_pilot.py'),'build'],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=240)
        run(['systemctl','start','iva@gateway','iva@engine'])
        config=json.loads((ROOT/'data/iva-pilot/control.json').read_text())
        for port,route in [(config['gateway_port'],'/health'),(config['iva_port'],'/')]:
            deadline=time.monotonic()+60
            while True:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}{route}',timeout=3) as r:
                        if r.status==200:break
                except OSError:pass
                if time.monotonic()>deadline:raise RuntimeError('Service health did not recover')
                time.sleep(1)
        if financial()!=before:raise RuntimeError('Financial operations changed')
        run(['systemctl','start','iva@telegram'])
        states=run(['systemctl','is-active','iva@gateway','iva@engine','iva@telegram','iva@research'],capture_output=True,text=True).stdout.splitlines()
        report={'installed':True,'credential_configured':(ROOT/'data/mail/yandex.json').exists(),'mode':'read_only','services':states,'financial_operations_unchanged':True,'release':str(release)}
        (release/'installation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
    except BaseException:
        if changed:
            subprocess.run(['systemctl','stop','iva@telegram','iva@engine','iva@gateway'],capture_output=True)
            for rel,sha in expected.items():
                target=ROOT/rel;saved=backup/rel
                if sha is None:target.unlink(missing_ok=True)
                else:shutil.copy2(saved,target);shutil.chown(target,user='iva',group='iva')
            output=APP/'.output'
            if output.resolve()!=Path('/opt/iva/data/iva-pilot/app/.output'):raise RuntimeError('Unexpected output path')
            shutil.rmtree(output);shutil.copytree(backup/'compiled-output',output)
            for p in [output,*output.rglob('*')]:shutil.chown(p,user='iva',group='iva')
        if stopped:subprocess.run(['systemctl','start','iva@gateway','iva@engine','iva@telegram'],capture_output=True)
        raise
    finally:lock.close()
if __name__=='__main__':main()
