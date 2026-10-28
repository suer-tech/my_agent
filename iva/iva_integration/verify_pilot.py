"""Bounded real gateway/SQLite verification. Synthetic writes restored afterward."""
from datetime import date,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger
PILOT=ROOT/'data/iva-pilot';config=json.loads((PILOT/'control.json').read_text())


def request(body,owner=False):
    token=config['owner_token' if owner else 'agent_token'];path='/owner/finance' if owner else '/finance'
    req=urllib.request.Request(f"http://127.0.0.1:{config['gateway_port']}"+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)


def main():
    isolation=json.loads((PILOT/'isolation.json').read_text());production=ROOT/'data/local-finance.sqlite3'
    before=hashlib.sha256(production.read_bytes()).hexdigest();assert before==isolation['production_sha256']
    path=PILOT/'finance/finance.sqlite3';saved=sqlite3.connect(':memory:');source=sqlite3.connect(path);source.backup(saved);source.close()
    report={}
    try:
        snapshot=request({'mode':'read','action':'snapshot'})
        assert len(snapshot['bank_schedules'])==2 and len(snapshot['credit_cards'])==2 and len(snapshot['reported_loans'])==2 and len(snapshot['utility_bills'])==4
        calendar=request({'mode':'read','action':'calendar','payload':{'start':'2026-10-19','end':'2026-11-30'}})
        assert sum(e.get('period_status')=='unknown' for e in calendar)==2
        assert any(e.get('unpaid_confirmed') for e in calendar)
        assert any(e.get('amount_is_estimate') for e in calendar)
        prefix='synthetic-pilot-verification-v1'
        payload={'name':'Synthetic pilot confirmation account','opening':'100.00','as_of':'2026-10-19'}
        proposal={'mode':'propose','action':'account','payload':payload,'source':prefix+'-account'}
        request(proposal);again=request(proposal);assert again['duplicate']
        pending=request({'mode':'pending'});key=next(p['id'] for p in pending if p['action']=='account' and 'Synthetic pilot confirmation account' in p['payload'])
        assert len(request({'mode':'read','action':'snapshot'})['accounts'])==len(snapshot['accounts'])
        try:request({'mode':'confirm','id':key});raise AssertionError('Model gained owner rights')
        except urllib.error.HTTPError as error:assert error.code==400
        request({'mode':'confirm','id':key},True)
        current=request({'mode':'read','action':'snapshot'});account=next(a['id'] for a in current['accounts'] if a['name']==payload['name'])
        request({'mode':'propose','action':'stage','payload':{'data':{'kind':'expense','amount':'1.23','date':'2026-10-20','account':account,'note':'Synthetic pilot only','ambiguities':[]}},'source':prefix+'-expense'})
        pending=request({'mode':'pending'});key=next(p['id'] for p in pending if p['action']=='stage' and 'Synthetic pilot only' in p['payload'])
        assert len(request({'mode':'read','action':'snapshot'})['operations'])==len(snapshot['operations'])
        request({'mode':'confirm','id':key},True);request({'mode':'confirm','id':key},True)
        result=request({'mode':'read','action':'snapshot'});assert len(result['operations'])==len(snapshot['operations'])+1
        # All real financial models integrated into one deterministic forecast on copy.
        ledger=Ledger(path)
        for row in ledger.rows('SELECT id FROM bank_schedules WHERE active=1'):ledger.bank_bind(row['id'],account)
        for row in ledger.rows('SELECT id FROM credit_cards WHERE active=1'):ledger.card_bind(row['id'],account)
        for row in ledger.rows('SELECT id FROM reported_loans WHERE active=1'):ledger.debt_bind(row['id'],account)
        for row in ledger.rows('SELECT id FROM utility_bills'):ledger.utility_bind(row['id'],account)
        forecast=ledger.forecast('2026-10-19','2026-11-30');assert forecast['has_estimated_amounts'] and len(forecast['uncertain_obligations'])==2
        assert any(e.get('unpaid_confirmed') for e in forecast['overdue_unsettled'])
        ledger.queue_reminders('2026-10-19','2026-11-30');assert len(ledger.snapshot()['operations'])==len(snapshot['operations'])+1
        docs=ledger.rows('SELECT path FROM source_documents');assert all(Path(d['path']).is_file() and str(PILOT) in d['path'] for d in docs)
        assert ledger.rows('PRAGMA integrity_check')==[{'integrity_check':'ok'}];ledger.db.close()
        report.update(finance_read=True,exact_graphs_cards_debts_utilities=True,proposal_without_write=True,repeat_proposal_idempotent=True,model_confirmation_rejected=True,owner_confirmation_applied=True,repeat_confirmation_no_duplicate=True,unified_forecast_on_bound_copy=True,variable_estimates_and_unknown_periods_preserved=True,original_documents_in_pilot=True)
    finally:
        target=sqlite3.connect(path);saved.backup(target);assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok';target.close();saved.close()
    assert hashlib.sha256(production.read_bytes()).hexdigest()==before
    assert not (PILOT/'app/data/codex-auth.json').exists()
    report.update(production_database_unchanged=True,synthetic_finance_mutations_restored=True,native_iva_oauth_tested=False,cli_login_copied_to_iva=False,telegram_enabled=False,windows_directory_fsync_supported=False)
    (PILOT/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))


if __name__=='__main__':main()
