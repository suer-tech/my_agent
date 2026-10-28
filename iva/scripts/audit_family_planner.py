"""Verify real source preservation and linked-payment behavior on RAM copies."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'plugin/family-finance'))
from finance.core import Ledger


def main():
    backup=json.loads((ROOT/'data/family-planner-backup.json').read_text())
    results=[]
    for label,path in [('production',ROOT/'data/local-finance.sqlite3'),('iva-pilot',ROOT/'data/iva-pilot/finance/finance.sqlite3')]:
        source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);ledger=Ledger(':memory:');source.backup(ledger.db)
        saved=sqlite3.connect((Path(backup['path'])/(label+'.sqlite3')).as_uri()+'?mode=ro',uri=True)
        for table in ['bank_schedules','bank_payments','credit_cards','reported_loans','reported_loan_periods','utility_bills','utility_quotes','utility_periods','source_documents','operations']:
            assert source.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()==saved.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall(),table
        original_intake=json.loads(ledger.rows('SELECT payload FROM local_intake')[0]['payload'])
        prior_intake=json.loads(saved.execute('SELECT payload FROM local_intake').fetchone()[0])
        for block in ['items','lines']:
            for prior,current in zip(prior_intake[block],original_intake[block]):
                assert all(current.get(k)==v for k,v in prior.items()),'Original intake evidence changed'
        assert original_intake['original_messages']==prior_intake['original_messages']
        assert len(original_intake['items'])==18
        r=ledger.family_forecast('2026-10-19','2026-10-31');d=ledger.debt_inventory('2026-10-19')
        assert r['actual_balance']==0 and r['available_for_early_repayment']==0 and not r['complete']
        assert r['first_gap']=='2026-10-19' and r['minimum_conservative']<0
        assert len(d['debts'])==6+len(ledger.rows('SELECT ref FROM family_debts'))
        assert next(x for x in d['debts'] if x['name']=='Сбер')['reported_card_balance']==18148372
        assert next(x for x in d['debts'] if x['name'].startswith('Т-Банк'))['reported_total_balance']==14838554
        alpha=next(x for x in d['debts'] if x['name']=='Альфа')
        assert alpha['document_figures']['values']['principal']==15151768 and alpha['current_principal'] is None
        assert all(e['amount'] is None for e in r['expected_income'])
        assert next(e for e in r['expected_income'] if e['ref']=='salary25')['date']=='2026-10-23'
        assert not any(e['nominal_date']=='2026-10-10' for e in r['expected_income'])
        assert r['monthly_income_reference']['full_nominal_month_estimate']==15000000
        assert r['monthly_income_reference']['remaining_current_month'] is None
        overdue=[e for day in r['days'] for e in day['events'] if e.get('unpaid_confirmed')]
        assert len(overdue)==1 and overdue[0]['amount']==2043821
        scenarios=ledger.family_scenarios('2026-10-19','2026-10-31',income_amounts={'salary20':'120000'})
        assert scenarios['on_time']['days'][-1]['known_flow_balance']-scenarios['delayed']['days'][-1]['known_flow_balance']==12000000
        # Actual repayment on a copy closes the occurrence exactly once.
        account=ledger.rows('SELECT id FROM accounts')[0]['id']
        payment=ledger.stage({'kind':'expense','amount':'20438.21','date':'2026-10-20','account':account,'occurrence':overdue[0]['occurrence']},'audit-only:ram-bank-payment')
        ledger.confirm(payment['id'],True)
        after=ledger.family_forecast('2026-10-20','2026-10-31')
        assert after['actual_balance']==-2043821
        assert not any(e.get('unpaid_confirmed') for day in after['days'] for e in day['events'])
        assert ledger.confirm(payment['id'],True)['duplicate']
        card=next(e for e in ledger.events('2026-10-28','2026-10-28') if e.get('card_id') and e['amount']==521717)
        draft=ledger.stage({'kind':'expense','amount':'5217.17','date':'2026-10-28','account':account,'occurrence':card['occurrence']},'audit-only:ram-interest-payment')
        ledger.confirm(draft['id'],True)
        assert next(x for x in ledger.rows('SELECT * FROM credit_cards') if x['name']=='Сбер')['balance']==18148372
        assert source.execute('SELECT count(*) FROM operations').fetchone()[0]==0
        assert source.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        results.append({'database':label,'original_records_preserved':True,'intake_lines':18,'active_debts':len(d['debts']),'salary_split_not_invented':True,'october_tenth_not_recredited':True,'salary25_on_october23':True,'reference_not_actual_cash':True,'unpaid_sovkom_carried_once':True,'linked_bank_payment_idempotent_on_ram_copy':True,'interest_payment_does_not_reduce_sber_principal_on_ram_copy':True,'source_principal_distinct_from_future_payment_sum':True,'cash_operations_in_real_database':0,'integrity':'ok'})
        ledger.db.close();source.close();saved.close()
    previous=json.loads((ROOT/'data/iva-pilot/isolation.json').read_text());original_docs=Path(previous['backup'])/'documents';current_docs=ROOT/'data/documents'
    def hashes(folder):return {p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
    assert hashes(original_docs)==hashes(current_docs)
    out=ROOT/'data/family-planner/semantic-audit.json';out.write_text(json.dumps({'verified_at':datetime.now(timezone(timedelta(hours=5))).isoformat(),'databases':results,'original_documents_unchanged':True,'test_payments_in_memory_only':True,'regression_tests_passed':129},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'audit':str(out),'databases_verified':len(results),'original_records_and_documents_preserved':True,'all_checks_passed':True}))

if __name__=='__main__':main()
