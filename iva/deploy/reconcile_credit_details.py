"""Recover the two owner-requested 19 October credit updates blocked by old codes."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger,Assistant
from local_finance.changes import apply_change

IDS=('6d64c6af','7881c518')


def fingerprint(l):
    rows=l.rows('SELECT * FROM operations ORDER BY id')
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def reconcile(l):
    before=fingerprint(l);results=[]
    identities={t:[r['id'] for r in l.rows('SELECT id FROM '+t+' ORDER BY id')] for t in ('credit_cards','reported_loans')}
    with l.transaction():
        for pid in IDS:
            rows=l.rows('SELECT * FROM local_pending WHERE id=?',(pid,))
            if len(rows)!=1:raise ValueError('Expected historical draft missing')
            row=rows[0];data=json.loads(row['payload'])
            expected='debt_save' if pid==IDS[0] else 'card_save'
            if row['action']!=expected:raise ValueError('Unexpected historical action')
            source='maintenance:credit-details:2026-10-20:'+pid
            result=apply_change(l,412850740,source,row['action'],data,'Сохрани ранее порученные сведения о кредитах','Сохрани ранее порученные сведения о кредитах')
            l.db.execute("UPDATE local_pending SET state='done' WHERE id=?",(pid,))
            # A retry of the original specialist job must describe the recovered
            # state instead of returning its obsolete request for a code.
            original='telegram:412850740:'+('608703904' if pid==IDS[0] else '608703906')+':0'
            for job in l.rows('SELECT result FROM local_finance_agent_jobs WHERE source=?',(original,)):
                cached=json.loads(job['result'] or '{}');cached.update({'ok':True,'text':'Сведения о кредите сохранены в реестре без повторного подтверждения. Фактических списаний нет.','reconciled':True})
                l.db.execute("UPDATE local_finance_agent_jobs SET state='done',result=? WHERE source=?",(json.dumps(cached,ensure_ascii=False),original))
            results.append({'draft':pid,'action':row['action'],'applied':result['applied'],'duplicate':result.get('duplicate',False)})
        if fingerprint(l)!=before:raise ValueError('Recovery must never change cash operations')
        for table,ids in identities.items():
            if [r['id'] for r in l.rows('SELECT id FROM '+table+' ORDER BY id')]!=ids:raise ValueError('Recovery created another debt')
    return {'cash_operations_unchanged':True,'debt_identities_unchanged':True,'results':results}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    path=ROOT/'data/local-finance.sqlite3'
    if path.as_posix()!='/opt/iva/data/local-finance.sqlite3':raise ValueError('Run on the canonical VPS only')
    live=sqlite3.connect(path)
    if args.apply:
        backup=ROOT/'data/server-backups'/('before-credit-recovery-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3')
        backup.parent.mkdir(parents=True,exist_ok=True)
        target=sqlite3.connect(backup);live.backup(target);target.close();backup.chmod(0o600)
        ledger=Ledger(path)
    else:
        ledger=Ledger(':memory:');live.backup(ledger.db)
    live.close()
    try:
        Assistant(ledger,None);report=reconcile(ledger)
        print(json.dumps({'applied_to_live':args.apply,**report},ensure_ascii=False))
        if args.apply:(ROOT/'data/credit-recovery-2026-10-20.json').write_text(json.dumps(report,ensure_ascii=False))
    finally:ledger.db.close()


if __name__=='__main__':main()
