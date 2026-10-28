"""Repair only the unimported Ozon source extraction, never the cash/debt ledger."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger
from local_finance.document_vision import originals
from local_finance.financial_screens import finalize,persist
DOCUMENT='af596bbf80ce8f8720606b554a55c0d8ff212f2e659a974d4ed0485368878fb7'
TABLES=('accounts','operations','bank_schedules','bank_payments','credit_cards','reported_loans','plans','rules','family_expenses','family_debts')


def financial_fingerprint(l):
    return hashlib.sha256(json.dumps({t:l.rows('SELECT * FROM '+t+' ORDER BY rowid') for t in TABLES},sort_keys=True).encode()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--release',required=True,type=Path);p.add_argument('--apply',action='store_true');a=p.parse_args()
    if ROOT.as_posix()!='/opt/iva':raise ValueError('Canonical VPS only')
    release=a.release.resolve();candidate=json.loads((release/'verified-extraction.json').read_text())
    report=json.loads((release/'model-verification.json').read_text());assert report['passed']
    l=Ledger(ROOT/'data/local-finance.sqlite3')
    if not a.apply:
        copy=Ledger(':memory:');l.db.backup(copy.db);l.db.close();l=copy
    try:
        before=financial_fingerprint(l)
        with l.transaction():
            old=l.rows('SELECT * FROM source_documents WHERE id=?',(DOCUMENT,))[0]
            if old['status']=='imported':raise ValueError('Schedule was imported meanwhile; do not replace it')
            with originals(l,DOCUMENT,storage_root=ROOT) as (info,images):assert info['hashes_verified'] and len(images)==4
            data=finalize(candidate)
            assert len(data['rows'])==17 and all(r['date'] is None for r in data['rows'])
            assert all(c['matches'] is True for c in data['arithmetic_checks'])
            prior=json.loads(old['extraction'])
            for key in ('original_members','vision_input_count'):
                if key in prior:data[key]=prior[key]
            persist(l,DOCUMENT,old['path'],'loan_schedule',4,data)
            assert financial_fingerprint(l)==before
        result={'applied_to_live':a.apply,'document':DOCUMENT,'rows':len(data['rows']),'pages':4,'years_unknown':True,'cash_and_debts_unchanged':True,'fingerprint':before,'revision_count':len(l.rows('SELECT id FROM source_document_revisions WHERE document_id=?',(DOCUMENT,)))}
        (release/('source-repair.json' if a.apply else 'source-dry-run.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
    finally:l.db.close()


if __name__=='__main__':main()
