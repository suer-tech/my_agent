"""Sanitized persisted state audit; no messages or bank personal details."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger
from local_finance.bot import OWNER
from local_finance import intake
l=Ledger(ROOT/'data/local-finance.sqlite3')
calendar=l.calendar('2026-10-18','2026-11-30')
draft=intake.context(l,OWNER)['draft']
bank=l.rows('SELECT s.id,s.active,s.version,count(p.id) rows_count,sum(p.unpaid_confirmed) unpaid FROM bank_schedules s JOIN bank_payments p ON p.schedule_id=s.id GROUP BY s.id')
cards=l.rows('SELECT id,due_day,balance,monthly_payment,monthly_interest FROM credit_cards WHERE active=1')
debts=l.rows('SELECT * FROM reported_loans WHERE active=1')
utilities=l.rows('SELECT * FROM utility_bills')
assert len(bank)==2 and sum(b['rows_count'] for b in bank)==95
assert all(b['active']==1 and b['version']==1 for b in bank)
assert len(cards)==2 and len(debts)==2 and len(utilities)==4
assert sum(b['unpaid'] for b in bank)==1
assert len(l.rows("SELECT * FROM reported_loan_periods WHERE period='2026-10' AND status='external_paid'"))==2
assert not l.rows('SELECT * FROM operations')
assert len(draft['items'])==18
assert len([i for i in draft['items'] if i.get('bank_schedule_id')])==2
assert all(i.get('do_not_create_approximate_payment') for i in draft['items'][:6])
assert not l.rows('SELECT id FROM rules WHERE end IS NULL') and not l.rows('SELECT id FROM plans WHERE cancelled=0')
assert len(l.rows("SELECT * FROM utility_periods WHERE period='2026-10' AND status='unknown'"))==2
reminders=l.rows("SELECT * FROM reminders WHERE state='pending'")
utility_checks=[]
for bill in utilities:
    es=[e for e in calendar if e.get('utility_bill_id')==bill['id']]
    next_known=next(e for e in es if e.get('period_status')!='unknown')
    due=l.rows('SELECT due,state FROM reminders WHERE id=?',(next_known['occurrence'],))[0]
    utility_checks.append({'bill_ref':bill['bill_ref'],'due_day':bill['due_day'],'next_due':next_known['date'],'reminder':due['due'],'reminder_state':due['state'],'unknown_october':any(e.get('period_status')=='unknown' for e in es),'future_estimate':any(e.get('amount_is_estimate') for e in es)})
try:l.forecast('2026-10-18','2026-11-30');blocked=False
except ValueError:blocked=True
assert blocked
report={'six_debts_saved_separately':True,'bank_graphs_active':2,'bank_rows':95,'bank_versions':[b['version'] for b in bank],'confirmed_unpaid_bank_rows':1,'cards_saved':2,'reported_debts_saved':2,'october_externally_paid_debts':2,'pending_bank_confirmations':len(l.rows("SELECT id FROM local_pending WHERE state='pending' AND action='bank_import'")),'original_intake_lines':18,'old_bank_approximation_lines_linked':2,'active_generic_rules':0,'active_generic_plans':0,'operations':0,'utilities':utility_checks,'cash_forecast_requires_accounts':blocked,'accounts':len(l.rows('SELECT id FROM accounts')),'telegram_sends_from_audit':0}
(ROOT/'docs/FINAL-PLANS-AUDIT.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report));l.db.close()
