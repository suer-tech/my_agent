"""Windows schedule/OS lock around official Iva night.ts; no replacement memory."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env,APP,PILOT
ZONE=timezone(timedelta(hours=5));STATUS=PILOT/'memory-scheduler.json'


@contextmanager
def lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    if stream.tell()==0:stream.write(b'0');stream.flush()
    stream.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:stream.close();raise RuntimeError('Another memory process holds the lock') from None
    try:yield
    finally:stream.close()


def state():
    return json.loads(STATUS.read_text()) if STATUS.exists() else {}


def write_state(value):
    temp=STATUS.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2),encoding='utf-8');temp.replace(STATUS)


def due(now):
    value=now.replace(hour=4,minute=0,second=0,microsecond=0)
    return value if now>=value else value-timedelta(days=1)


def next_run(now):return due(now)+timedelta(days=1)


def ensure_gateway():
    config=json.loads((PILOT/'control.json').read_text())
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{config['gateway_port']}/health",timeout=3) as r:
            if r.status==200:return
    except Exception:pass
    subprocess.run([sys.executable,str(ROOT/'iva_integration/run_pilot.py'),'start'],cwd=ROOT,capture_output=True,timeout=100,check=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)


def run_native(app=APP,vault=None,data=None,day=None):
    app=Path(app);vault=Path(vault or app/'vault');data=Path(data or app/'data');data.mkdir(parents=True,exist_ok=True)
    with lock(app/'.memory.lock'):
        childenv=env();childenv['ASSISTANT_VAULT_DIR']=str(vault);childenv['ASSISTANT_DATA_DIR']=str(data);childenv['IVA_MEMORY_LOCK_HELD']='1';childenv['IVA_JOB_STOP_AT']=str(int((time.time()+900)*1000))
        for key in list(childenv):
            if key.startswith('TELEGRAM_'):childenv.pop(key)
        command=['node',str(app/'scripts/memory/night.ts')]+([day] if day else [])
        logfile=data/'native-night.log'
        with logfile.open('ab') as log:
            process=subprocess.Popen(command,cwd=app,env=childenv,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:code=process.wait(timeout=930)
            except subprocess.TimeoutExpired:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                else:process.kill()
                code=124
        return {'exit_code':code,'log':str(logfile),'native_script':str(app/'scripts/memory/night.ts')}


def maintenance():
    # Official deterministic graph + scoped Git commit; no remote creation/push.
    result=subprocess.run(['node',str(ROOT/'iva_integration/memory-maintenance.ts')],cwd=APP,env=env(),capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    return result.returncode==0


def tick():
    now=datetime.now(ZONE);current=state();lastdue=due(now)
    current.update(pid=os.getpid(),heartbeat=now.isoformat(),timezone='Asia/Yekaterinburg',scheduled_local_time='04:00',next_run=next_run(now).isoformat(),enabled=True)
    retry=datetime.fromisoformat(current['retry_after']) if current.get('retry_after') else None
    if current.get('last_successful_due')!=lastdue.isoformat() and (not retry or now>=retry):
        current.update(running=True,last_started=now.isoformat());write_state(current)
        try:
            ensure_gateway();result=run_native();ok=result['exit_code']==0
            if ok:ok=maintenance()
            current.update(last_result=result,last_finished=datetime.now(ZONE).isoformat(),last_run_ok=ok)
            if ok:current.update(last_successful_due=lastdue.isoformat(),retry_after=None)
            else:current['retry_after']=(datetime.now(ZONE)+timedelta(minutes=15)).isoformat()
        except Exception as error:
            current.update(last_run_ok=False,error_type=type(error).__name__,retry_after=(datetime.now(ZONE)+timedelta(minutes=15)).isoformat())
        finally:current['running']=False
    write_state(current)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','loop','status','run']);parser.add_argument('--day');parser.add_argument('--vault');parser.add_argument('--data');a=parser.parse_args()
    if a.action in ('start','loop','run') and (ROOT/'data/remote-deployment.json').exists():
        print('Local memory jobs are disabled: Iva is deployed on VPS.');return
    if a.action=='status':print(json.dumps(state()));return
    if a.action=='stop':(PILOT/'memory-scheduler.stop').touch();print('Memory scheduler stop requested');return
    if a.action=='run':
        ensure_gateway();print(json.dumps(run_native(vault=a.vault,data=a.data,day=a.day)));return
    if a.action=='start':
        stop=PILOT/'memory-scheduler.stop'
        if stop.exists():stop.unlink()
        with (PILOT/'memory-scheduler.log').open('ab') as log:
            subprocess.Popen([sys.executable,__file__,'loop'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        print('Hidden memory scheduler start requested');return
    try:
        with lock(PILOT/'memory-scheduler.lock'):
            while not (PILOT/'memory-scheduler.stop').exists():
                tick()
                for _ in range(60):
                    if (PILOT/'memory-scheduler.stop').exists():break
                    time.sleep(1)
    except RuntimeError:return


if __name__=='__main__':main()
