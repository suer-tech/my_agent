"""Hidden, single-owner Telegram transport supervisor and status commands."""
import argparse
from datetime import datetime,timezone,timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from iva_integration.memory_scheduler import lock
from iva_integration.run_pilot import env
STATE=ROOT/'data/iva-telegram';ZONE=timezone(timedelta(hours=5))


def process_info(pid):
    if os.name!='nt':return None
    code=f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; if($p){{@{{pid=$p.ProcessId;started=$p.CreationDate.ToUniversalTime().ToString('o');command=$p.CommandLine}} | ConvertTo-Json -Compress}}"
    r=subprocess.run(['powershell','-NoProfile','-Command',code],capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    try:return json.loads(r.stdout.decode('utf-8-sig'))
    except ValueError:return None


def alive(record,kind):
    info=process_info(record.get('pid',0))
    expected='telegram_runtime.py' if kind=='supervisor' else 'local_finance.iva_telegram'
    return bool(info and expected in info.get('command','') and info['started']==record.get('started'))


def services_ok():
    cfg=json.loads((ROOT/'data/iva-pilot/control.json').read_text())
    for key,path in [('iva_port','/'),('gateway_port','/health')]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{cfg[key]}{path}",timeout=3) as r:
                if r.status!=200:return False
        except Exception:return False
    return True


def ensure_services():
    if services_ok():return
    # Managed runtime stop verifies each process identity before terminating it.
    for action in ['stop','start']:
        r=subprocess.run([sys.executable,str(ROOT/'iva_integration/run_pilot.py'),action],cwd=ROOT,env=env(),capture_output=True,timeout=100,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if r.returncode:raise RuntimeError('Iva runtime recovery failed')
    if not services_ok():raise RuntimeError('Iva runtime unavailable')


def write_record(kind,pid):
    info=process_info(pid)
    if not info:raise RuntimeError('Unable to verify process start time')
    record={'pid':pid,'started':info['started'],'role':kind}
    (STATE/(kind+'.json')).write_text(json.dumps(record),encoding='utf-8');return record


def status():
    remote=ROOT/'data/remote-deployment.json'
    if remote.exists():
        print(json.dumps({'deployment':'remote','local_runtime_disabled':True,**json.loads(remote.read_text(encoding='utf-8'))},ensure_ascii=False,indent=2));return
    result={'checked_at':datetime.now(ZONE).isoformat(),'engine':'real-iva-eve','services_responding':services_ok()}
    for kind in ['supervisor','worker']:
        path=STATE/(kind+'.json');record=json.loads(path.read_text()) if path.exists() else {};result[kind]={**record,'identity_and_start_time_verified':alive(record,kind) if record else False}
    path=STATE/'status.json'
    if path.exists():result['worker_status']=json.loads(path.read_text(encoding='utf-8'))
    print(json.dumps(result,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','status','supervise']);a=parser.parse_args();STATE.mkdir(exist_ok=True)
    if a.action in ('start','supervise') and (ROOT/'data/remote-deployment.json').exists():
        print('Local Telegram is disabled: Iva is deployed on VPS. Reconcile server data before any rollback.');return
    if a.action=='status':status();return
    if a.action=='stop':(STATE/'stop').touch();print('Telegram stop requested; local memory remains enabled');return
    if a.action=='start':
        path=STATE/'supervisor.json'
        if path.exists() and alive(json.loads(path.read_text()),'supervisor'):print('Iva Telegram supervisor is already running');return
        stop=STATE/'stop'
        if stop.exists():stop.unlink()
        with (STATE/'supervisor.log').open('ab') as out:
            child=subprocess.Popen([sys.executable,__file__,'supervise'],cwd=ROOT,env=env(),stdin=subprocess.DEVNULL,stdout=out,stderr=out,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        write_record('supervisor',child.pid);print('Iva Telegram hidden start requested');return
    with lock(STATE/'supervisor.lock'):
        write_record('supervisor',os.getpid());worker=None;last_health=0
        while not (STATE/'stop').exists():
            if worker is None or worker.poll() is not None:
                try:
                    ensure_services()
                    with (STATE/'worker.log').open('ab') as out:
                        worker=subprocess.Popen([sys.executable,'-m','local_finance.iva_telegram'],cwd=ROOT,env=env(),stdin=subprocess.DEVNULL,stdout=out,stderr=out,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    write_record('worker',worker.pid)
                except Exception as error:
                    with (STATE/'supervisor.log').open('a') as out:out.write(datetime.now(ZONE).isoformat()+' '+type(error).__name__+'\n')
            if time.monotonic()-last_health>60:
                try:ensure_services()
                except Exception:pass
                last_health=time.monotonic()
            for _ in range(15):
                if (STATE/'stop').exists():break
                time.sleep(1)
        if worker and worker.poll() is None:
            try:worker.wait(timeout=660) # Finish an already accepted turn; stop flag ends polling.
            except subprocess.TimeoutExpired:pass


if __name__=='__main__':
    try:main()
    except Exception as error:print('Runtime controller: '+type(error).__name__);raise SystemExit(1)
