"""Reconcile the specifically reported failed owner document, first on a copy."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger,Assistant
from local_finance.document_updates import apply_existing_debt_document
from local_finance.bot import OWNER
UID=608703915
SOURCE=f'telegram:{OWNER}:{UID}:0'
live=ROOT/'data/local-finance.sqlite3'
apply='--apply' in sys.argv
with tempfile.TemporaryDirectory(prefix='statement-recovery-',dir=ROOT/'data') as tmp:
    copy=Path(tmp)/'ledger.sqlite3'
    with sqlite3.connect(live) as src,sqlite3.connect(copy) as dst:src.backup(dst)
    if apply:
        backup=ROOT/'data/tbank-before-document-recovery.sqlite3'
        if not backup.exists():
            with sqlite3.connect(live) as src,sqlite3.connect(backup) as dst:src.backup(dst)
            backup.chmod(0o600)
    l=Ledger(live if apply else copy);Assistant(l,None)
    operations=l.rows('SELECT * FROM operations ORDER BY id')
    periods=l.rows('SELECT * FROM reported_loan_periods ORDER BY loan_id,period')
    ids=l.rows('SELECT id,debt_ref FROM reported_loans ORDER BY id')
    job=json.loads(l.rows('SELECT result FROM local_finance_agent_jobs WHERE source=?',(SOURCE,))[0]['result'])
    calls=[json.loads(s['signature']) for s in job['steps'] if s['tool']=='finance_apply' and not s['ok']]
    assert len(calls)==1
    args=json.loads(calls[0]['arguments_json']);payload=json.loads(args['payload_json'])
    assert args['action']=='debt_save' and payload['reported_total_balance']=='148505.64'
    with l.transaction():
        state=l.rows('SELECT state FROM local_updates WHERE id=?',(UID,))[0]['state']
        assert state=='done'
        # Explicit repair of this existing authenticated envelope after the
        # owner's report here. No new/fabricated Telegram message or polling ID.
        l.db.execute("UPDATE local_updates SET state='processing' WHERE id=?",(UID,))
        result=apply_existing_debt_document(l,OWNER,SOURCE,args['action'],payload)
        repeat=apply_existing_debt_document(l,OWNER,SOURCE,args['action'],payload)
        l.db.execute('UPDATE local_updates SET state=? WHERE id=?',(state,UID))
        assert operations==l.rows('SELECT * FROM operations ORDER BY id')
        assert periods==l.rows('SELECT * FROM reported_loan_periods ORDER BY loan_id,period')
        assert ids==l.rows('SELECT id,debt_ref FROM reported_loans ORDER BY id')
        assert repeat['duplicate']
        l.audit('document_authorization_repair',{'source':SOURCE,'document_update':result,'owner_requested_repair':True})
    row=l.rows('SELECT reported_total_balance,reported_as_of FROM reported_loans WHERE debt_ref=?',(payload['debt_ref'],))[0]
    report={'applied_to_live':apply,'result':result,'repeat_duplicate':repeat['duplicate'],
            'operations_unchanged':True,'payment_statuses_unchanged':True,'loan_ids_unchanged':True,
            'readback':row}
    out=ROOT/('data/tbank-document-recovery.json' if apply else 'data/tbank-document-recovery-dry.json')
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2));out.chmod(0o600)
    print(json.dumps(report,ensure_ascii=False))
    l.db.close()
