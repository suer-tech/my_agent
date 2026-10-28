"""Restore pre-cutover code/config only. Never restore financial data or OAuth tokens."""
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path('/opt/iva')
BACKUP=ROOT/'data/native-cutover-backup'
if __name__=='__main__':
    assert ROOT==Path(__file__).resolve().parents[1]
    state=json.loads((ROOT/'data/iva-telegram/session.json').read_text())
    assert not state.get('active_source'),'Wait for the current owner request'
    subprocess.run(['systemctl','stop','iva@telegram','iva@memory'],check=True)
    subprocess.run(['systemctl','stop','iva@engine','iva@gateway'],check=True)
    for name in json.loads((BACKUP/'manifest.json').read_text()):
        src=BACKUP/name
        if src.exists():
            shutil.copy2(src,ROOT/name)
    (ROOT/'data/iva-provider.json').unlink(missing_ok=True)
    subprocess.run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),
                    str(ROOT/'iva_integration/run_pilot.py'),'build'],cwd=ROOT,check=True)
    subprocess.run(['systemctl','start','iva@gateway','iva@engine','iva@telegram','iva@memory'],check=True)
    print('CLI provider restored; current ledger, memory, queue and subscription login preserved')
