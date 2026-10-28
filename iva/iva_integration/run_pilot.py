"""Start/stop only the isolated Iva pilot processes, with allowlisted environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import time
ROOT=Path(__file__).resolve().parents[1];PILOT=ROOT/'data/iva-pilot';APP=PILOT/'app'


def verified_process(record):
    if os.name!='nt':return True
    pid=int(record['pid'])
    command=f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}';if($p){{@{{command=$p.CommandLine;started=$p.CreationDate.ToUniversalTime().ToString('o')}}|ConvertTo-Json -Compress}}"
    result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
    try:value=json.loads(result.stdout.decode('utf-8-sig'))
    except ValueError:return None
    expected=str(ROOT/'iva_integration/gateway.py') if record['name']=='gateway' else 'node_modules/eve/bin/eve.js'
    if expected.replace('\\','/').casefold() not in value.get('command','').replace('\\','/').casefold():return None
    if record.get('started') and record['started']!=value['started']:return None
    return value


def env():
    result={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','HOME','LANG','CODEX_HOME')}
    for line in (APP/'.env').read_text(encoding='utf-8').splitlines():
        if '=' in line:k,v=line.split('=',1);result[k]=v
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','status','build']);a=parser.parse_args()
    if a.action=='start' and (ROOT/'data/remote-deployment.json').exists():
        print('Local Iva engine is disabled: the active deployment is on VPS.');return
    config=json.loads((PILOT/'control.json').read_text());record=PILOT/'processes.json'
    if a.action=='build':
        subprocess.run(['node','scripts/init-vault.mjs'],cwd=APP,env=env(),check=True)
        subprocess.run(['node','node_modules/eve/bin/eve.js','build'],cwd=APP,env=env(),check=True);return
    if a.action=='stop':
        if record.exists():
            for proc in json.loads(record.read_text()):
                if not verified_process(proc):continue
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc['pid']),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    try:os.kill(proc['pid'],15)
                    except ProcessLookupError:pass
        print('Pilot stop requested');return
    if a.action=='status':
        for name,port in [('gateway',config['gateway_port']),('iva',config['iva_port'])]:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}'+('/health' if name=='gateway' else '/'),timeout=3) as response:print(name,response.status)
            except Exception:print(name,'unavailable')
        return
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    import socket
    occupied=[]
    for port in (config['gateway_port'],config['iva_port']):
        with socket.socket() as probe:
            probe.settimeout(.5);occupied.append(probe.connect_ex(('127.0.0.1',port))==0)
    if any(occupied):
        if all(occupied):print('Pilot is already running; no duplicate processes started');return
        raise RuntimeError('One pilot port is occupied. Inspect status before starting again.')
    children=[]
    for name,command,cwd in [('gateway',[sys.executable,str(ROOT/'iva_integration/gateway.py')],ROOT),('iva',['node','node_modules/eve/bin/eve.js','start','--host','127.0.0.1','--port',str(config['iva_port'])],APP)]:
        with (PILOT/(name+'.stdout.log')).open('ab') as out,(PILOT/(name+'.stderr.log')).open('ab') as error:
            process=subprocess.Popen(command,cwd=cwd,env=env(),stdin=subprocess.DEVNULL,stdout=out,stderr=error,creationflags=flags)
        child={'name':name,'pid':process.pid};identity=verified_process(child)
        if identity and isinstance(identity,dict):child['started']=identity['started']
        children.append(child)
    record.write_text(json.dumps(children),encoding='utf-8')
    for name,port in [('gateway',config['gateway_port']),('iva',config['iva_port'])]:
        deadline=time.monotonic()+45
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}'+('/health' if name=='gateway' else '/'),timeout=2) as response:
                    if response.status==200:break
            except Exception:pass
            if time.monotonic()>deadline:raise RuntimeError(name+' did not become ready; see private pilot logs')
            time.sleep(.3)
    print('Iva session engine and model/finance gateway ready')


if __name__=='__main__':
    sys.path.insert(0,str(ROOT))
    if len(sys.argv)>1 and sys.argv[1] in ('start','stop'):
        from local_finance.bot import ProcessLock
        try:
            with ProcessLock(str(PILOT/'services.lock')):main()
        except RuntimeError:
            print('Another managed runtime action is in progress; no duplicate launch')
    else:main()
