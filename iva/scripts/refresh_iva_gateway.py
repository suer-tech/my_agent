"""Refresh the Python gateway only when Telegram has no turn in flight."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env,verified_process
from local_finance.bot import ProcessLock


def main():
    pilot=ROOT/'data/iva-pilot'
    with ProcessLock(str(pilot/'services.lock')):
        state=json.loads((ROOT/'data/iva-telegram/session.json').read_text(encoding='utf-8'))
        db=sqlite3.connect((ROOT/'data/local-finance.sqlite3').as_uri()+'?mode=ro',uri=True)
        try:busy=db.execute("SELECT COUNT(*) FROM local_updates WHERE state IN ('processing','ready','sending')").fetchone()[0]
        finally:db.close()
        if state.get('active_source') or busy:raise RuntimeError('An owner turn is still active; gateway was not stopped')
        path=pilot/'processes.json';records=json.loads(path.read_text(encoding='utf-8'))
        old=next(p for p in records if p['name']=='gateway')
        assert verified_process(old),'Gateway process identity mismatch'
        cfg=json.loads((pilot/'control.json').read_text(encoding='utf-8'))
        subprocess.run(['taskkill','/PID',str(old['pid']),'/T','/F'],capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
        with (pilot/'gateway.stdout.log').open('ab') as out,(pilot/'gateway.stderr.log').open('ab') as error:
            child=subprocess.Popen([sys.executable,str(ROOT/'iva_integration/gateway.py')],cwd=ROOT,env=env(),stdin=subprocess.DEVNULL,stdout=out,stderr=error,creationflags=subprocess.CREATE_NO_WINDOW)
        new={'name':'gateway','pid':child.pid};identity=verified_process(new)
        assert identity,'Replacement gateway identity missing'
        new['started']=identity['started']
        records=[new if p['name']=='gateway' else p for p in records]
        path.write_text(json.dumps(records),encoding='utf-8')
        for _ in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:'+str(cfg['gateway_port'])+'/health',timeout=1) as response:
                    if response.status==200:break
            except OSError:time.sleep(.2)
        else:raise RuntimeError('Replacement gateway is not healthy')
        print(json.dumps({'gateway_restarted':True,'old_pid':old['pid'],'new_pid':child.pid,'engine_not_restarted':True,'telegram_worker_not_restarted':True}))


if __name__=='__main__':main()
