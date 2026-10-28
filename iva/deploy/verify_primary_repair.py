"""Exercise live primary model and forced fallback with an isolated empty ledger."""
import json
import sys
from pathlib import Path
sys.path.insert(0,'/opt/iva')
from local_finance.assistant import Ledger,setup as assistant_setup
from local_finance.opencode_cli import OpenCode,OpenCodeError
from iva_integration.primary_engine import PrimaryBridge,PROMPT
from local_finance.iva_telegram import EveBridge

root=Path('/opt/iva/data/repair-20261024/primary-v2');root.mkdir(parents=True,exist_ok=True)
results={}
cli=OpenCode()
try:
    decision=cli.decide(PROMPT+'\nCURRENT_INPUT={"requires_workflow":false,"message":"Назови столицу Франции одним словом","history":[]}')
    results['opencode']={'ok':True,'decision':decision,'metrics':cli.last_execution}
except Exception as exc:results['opencode']={'ok':False,'error_type':type(exc).__name__,'metrics':cli.last_execution}

class Unavailable:
    last_execution={'failure':{'stage':'synthetic_provider_failure'}}
    def __init__(self):self.calls=0
    def decide(self,prompt):
        self.calls+=1
        raise OpenCodeError('Forced fallback diagnostic')

ledger=Ledger(root/'isolated.sqlite3');assistant_setup(ledger.db)
try:
    with ledger.db:ledger.db.execute("INSERT OR REPLACE INTO local_meta VALUES('primary_engine','opencode')")
    unavailable=Unavailable();announcements=[]
    bridge=PrimaryBridge(ledger,EveBridge(state_dir=root/'session',skip_daily=True),cli=unavailable)
    bridge.on_progress=lambda value:announcements.append(value)
    try:
        answer=bridge.send_owner('Техническая проверка. Назови столицу Франции одним словом, ничего не изменяй.','diagnostic:primary-fallback-repair-20261024-v2','Назови столицу Франции')
        results['fallback']={'ok':True,'answer':answer}
        second=bridge.send_owner('Сколько будет два плюс два? Ответь одним числом, никаких действий.','diagnostic:primary-fallback-repair-20261024-v2-next','Сколько будет два плюс два?')
        results['cooldown']={'ok':True,'answer':second,'provider_calls':unavailable.calls,'fallback_announcements':sum(e.get('id')=='primary-fallback' for e in announcements)}
    except Exception as exc:results['fallback']={'ok':False,'error_type':type(exc).__name__}
finally:ledger.db.close()
(root/'verification.json').write_text(json.dumps(results,ensure_ascii=False))
