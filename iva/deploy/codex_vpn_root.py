#!/usr/bin/python3
"""Minimal sudo entry: enter ONE namespace, then drop to iva before Codex."""
import os
from pathlib import Path
import sys
import signal
import subprocess
import time
if os.geteuid()!=0:raise PermissionError('Use the Codex VPN shim')
home=Path(sys.argv[1]).resolve()
base=Path('/home/iva/.codex').resolve()
if home!=base and base not in home.parents:raise PermissionError('Unexpected Codex home')
caller=int(sys.argv[2])
if caller<=1:raise ValueError('Invalid caller')
caller_path=Path('/proc')/str(caller)/'stat'
identity=caller_path.read_text().split(') ',1)[1].split()[19]
command=['/usr/sbin/ip','netns','exec','iva-codex','/usr/sbin/runuser','-u','iva','--',
         '/usr/bin/env','CODEX_HOME='+str(home),'HOME=/home/iva','PATH=/usr/local/bin:/usr/bin:/bin','LANG=C.UTF-8',
         '/usr/local/bin/codex',*sys.argv[3:]]
process=subprocess.Popen(command,start_new_session=True)
deadline=time.monotonic()+300
while process.poll() is None:
    try:
        info=caller_path.read_text().split(') ',1)[1].split()
        alive=info[19]==identity and info[0]!='Z'
    except (OSError,IndexError):alive=False
    if not alive or time.monotonic()>deadline:
        os.killpg(process.pid,signal.SIGTERM)
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
        raise SystemExit(124)
    time.sleep(.2)
raise SystemExit(process.returncode)
