"""Two real OpenCode routing probes; all owner/state/workflow writes are isolated."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger,setup
from local_finance.opencode_cli import OpenCode,MODEL
from iva_integration.primary_engine import PrimaryBridge,OWNER
from iva_integration import runtime_diagnostics

RELEASE=ROOT/'data/opencode-repair-20261025'
OUTPUT=RELEASE/'verification'
WORKFLOW_SENTINEL='DIAGNOSTIC_WORKFLOW_NOT_EXECUTED'
CASES=(('arithmetic','Сколько будет 2+2? Ответь только числом.','reply'),
       ('current_weather','Посмотри прогноз погоды сейчас в Екатеринбурге','workflow'))


class WorkflowStub:
    skip_daily=True
    def __init__(self):self.calls=[];self.on_progress=None;self.on_activity=None
    def send(self,text,source):
        self.calls.append({'source':source,'text_sha256':hashlib.sha256(text.encode()).hexdigest()})
        return WORKFLOW_SENTINEL


class CountingClient:
    def __init__(self,client):self.client=client;self.calls=0;self.last_execution={}
    def decide(self,prompt):
        self.calls+=1
        try:return self.client.decide(prompt)
        finally:self.last_execution=dict(self.client.last_execution)


def envelope(ledger,identity,text):
    """Synthetic private-owner provenance exists ONLY in the fresh fixture DB."""
    source=f'telegram:{OWNER}:{identity}:0'
    message={'message_id':identity,'date':int(time.time()),'from':{'id':OWNER},
             'chat':{'id':OWNER,'type':'private'},'text':text}
    with ledger.db:
        ledger.db.execute('INSERT INTO local_updates(id,chat,text,state,metadata) VALUES(?,?,?,\'processing\',?)',
                          (identity,OWNER,text,json.dumps({'diagnostic_fixture':True})))
        ledger.db.execute('INSERT INTO local_telegram_messages(id,message_id,message_json) VALUES(?,?,?)',
                          (identity,identity,json.dumps(message,ensure_ascii=False)))
    return source


def verify(directory,client):
    directory=Path(directory)
    database=directory/'isolated-ledger.sqlite3'
    if database.exists() or database.is_symlink():raise RuntimeError('opencode_probe_requires_fresh_database')
    ledger=Ledger(database);database.chmod(0o600);setup(ledger.db)
    counted=CountingClient(client);fallback=WorkflowStub();events=[];results=[]
    try:
        with ledger.db:ledger.db.execute("INSERT INTO local_meta VALUES('primary_engine','opencode')")
        bridge=PrimaryBridge(ledger,fallback,cli=counted,root=directory)
        def diagnostic_event(component,**fields):
            events.append({'component':component,**{k:v for k,v in fields.items() if k in runtime_diagnostics.FIELDS}})
        with patch.object(runtime_diagnostics,'event',diagnostic_event):
            for index,(name,text,expected) in enumerate(CASES,1):
                source=envelope(ledger,950000000+index,text)
                before=counted.calls;workflow_before=len(fallback.calls)
                error_type=None;answer=None;cached=None
                try:
                    answer=bridge.send_owner(text,source,text)
                    after=counted.calls
                    cached=bridge.send_owner(text,source,text)
                except Exception as error:
                    error_type=type(error).__name__;after=counted.calls
                rows=ledger.rows('SELECT state,decision,metrics FROM local_primary_turns WHERE source=?',(source,))
                row=rows[0] if rows else {}
                decision=json.loads(row.get('decision') or '{}');metrics=json.loads(row.get('metrics') or '{}')
                actual='opencode' if expected=='reply' else 'opencode_then_workflow'
                cost=metrics.get('cost')
                criteria={'one_real_model_call':after-before==1,
                          'cached_source_no_second_model_call':counted.calls==after and cached==answer and error_type is None,
                          'actual_engine_expected':metrics.get('actual')==actual,
                          'exact_actual_model':metrics.get('actual_model')==MODEL,
                          'text_only_native_adapter':metrics.get('transport')=='opencode-cli-serve',
                          'reported_cost_zero':type(cost) in (int,float) and cost==0,
                          'route_expected':decision.get('route')==expected,
                          'done_checkpoint':row.get('state')=='done',
                          'reply_or_stub_result':str(answer).strip()=='4' if expected=='reply' else answer==WORKFLOW_SENTINEL,
                          'workflow_only_for_tool_request':len(fallback.calls)-workflow_before==(1 if expected=='workflow' else 0)}
                results.append({'name':name,'expected_route':expected,'passed':all(criteria.values()),'criteria':criteria,
                                'error_type':error_type,'answer':answer,'decision':decision,
                                'metrics':{key:metrics[key] for key in ('selected','actual','model','actual_model','transport',
                                    'elapsed_ms','frontend_ms','cost','tokens','failure') if key in metrics}})
        operations=ledger.db.execute('SELECT count(*) FROM operations').fetchone()[0]
        checks={'two_calls_total':counted.calls==2,'one_stub_call_total':len(fallback.calls)==1,
                'no_financial_operations':operations==0,
                'no_daily_vault_created':not (directory/'data/iva-pilot/app/vault').exists()}
        return {'at':datetime.now(timezone.utc).isoformat(),'passed':all(row['passed'] for row in results) and all(checks.values()),
                'cases':results,'criteria':checks,'model_calls':counted.calls,'workflow_stub_calls':len(fallback.calls),
                'financial_operations':operations,'diagnostic_events':events,
                'limits':['Synthetic owner provenance was inserted only into this new diagnostic ledger.',
                          'Weather routing was checked; the workflow stub did not fetch weather or invoke any tool.',
                          'No Telegram, production local_meta, owner vault, or financial database writes.',
                          'The existing adapter rejects native tool requests; this live probe did not request tool execution.']}
    finally:ledger.db.close()


@contextmanager
def deployment_read_lock():
    import fcntl
    with (ROOT/'data/application-deploy.lock').open('rb') as stream:
        fcntl.flock(stream.fileno(),fcntl.LOCK_SH|fcntl.LOCK_NB)
        try:yield
        finally:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def prepare_output():
    if not RELEASE.is_dir() or RELEASE.is_symlink() or OUTPUT.is_symlink():raise RuntimeError('invalid_probe_directory')
    OUTPUT.mkdir(mode=0o700,exist_ok=True)
    if OUTPUT.resolve()!=RELEASE.resolve()/'verification':raise RuntimeError('invalid_probe_directory')
    for name in ('isolated-ledger.sqlite3','isolated-ledger.sqlite3-wal','isolated-ledger.sqlite3-shm','recovery-report.json'):
        if (OUTPUT/name).exists() or (OUTPUT/name).is_symlink():raise RuntimeError('opencode_probe_requires_fresh_directory')
    return OUTPUT


def main():
    if os.name!='posix' or ROOT!=Path('/opt/iva') or len(sys.argv)!=1:raise RuntimeError('run_probe_only_on_vps_without_arguments')
    directory=prepare_output()
    with deployment_read_lock():
        report=verify(directory,OpenCode(ROOT))
        report['source_sha256']={rel:hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() for rel in (
            'local_finance/opencode_cli.py','local_finance/opencode_service.py','iva_integration/primary_engine.py',
            'deploy/verify_opencode_recovery.py')}
        target=directory/'recovery-report.json';temporary=target.with_name(target.name+'.'+uuid.uuid4().hex)
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target)
    print(json.dumps({'passed':report['passed'],'cases':[{'name':row['name'],'passed':row['passed'],'criteria':row['criteria']} for row in report['cases']],
                      'model_calls':report['model_calls'],'financial_operations':report['financial_operations']},ensure_ascii=False))
    return 0 if report['passed'] else 2


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'passed':False,'error_type':type(error).__name__}))
        raise SystemExit(2)
