"""Foreground entry points managed by Linux systemd, with private app env."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env,APP,PILOT


def main():
    role=sys.argv[1]
    child=env()
    if role != "engine": child.pop("NODE_OPTIONS",None)
    os.chdir(ROOT)
    if role=='gateway':command=[sys.executable,str(ROOT/'iva_integration/gateway.py')]
    elif role=='engine':
        generation=PILOT/'engine-generation.json'
        generation.write_text(json.dumps({'generation':str(time.time_ns()),'pid':os.getpid()}));generation.chmod(0o600)
        config=json.loads((PILOT/'control.json').read_text())
        os.chdir(APP)
        command=['node','node_modules/eve/bin/eve.js','start','--host','127.0.0.1','--port',str(config['iva_port'])]
    elif role=='research':command=[sys.executable,'-m','iva_integration.tender_worker']
    elif role=='scheduler':command=[sys.executable,'-m','iva_integration.scheduled_worker']
    elif role in ('telegram','memory'):
        config=json.loads((PILOT/'control.json').read_text())
        deadline=time.monotonic()+90
        for key,path in [('gateway_port','/health'),('iva_port','/')]:
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{config[key]}{path}",timeout=2) as response:
                        if response.status==200:break
                except OSError:pass
                if time.monotonic()>deadline:raise RuntimeError('Iva dependency unavailable')
                time.sleep(.5)
        stop=ROOT/('data/iva-telegram/stop' if role=='telegram' else 'data/iva-pilot/memory-scheduler.stop')
        stop.unlink(missing_ok=True)
        command=[sys.executable,'-m','local_finance.iva_telegram'] if role=='telegram' else [sys.executable,str(ROOT/'iva_integration/memory_scheduler.py'),'loop']
    else:raise ValueError('Unknown service role')
    os.execvpe(command[0],command,child)


if __name__=='__main__':main()
