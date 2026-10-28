"""Separate user-reported instalment obligations and externally fulfilled periods."""
import json
from datetime import date
from .core import local_today,money,day,ident,month_day,next_month


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS reported_loans(id TEXT PRIMARY KEY,debt_ref TEXT UNIQUE NOT NULL,name TEXT NOT NULL,monthly_payment INTEGER NOT NULL,due_day INTEGER NOT NULL,planning_from_period TEXT NOT NULL,account TEXT REFERENCES accounts(id),principal_balance INTEGER,annual_rate TEXT,term_months INTEGER,payment_allocation TEXT,reported_as_of TEXT NOT NULL,source TEXT NOT NULL,notes TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS reported_loan_periods(loan_id TEXT NOT NULL REFERENCES reported_loans(id),period TEXT NOT NULL,due_date TEXT NOT NULL,amount INTEGER NOT NULL,status TEXT NOT NULL,payment_date TEXT,payment_account TEXT REFERENCES accounts(id),source TEXT NOT NULL,PRIMARY KEY(loan_id,period));
    CREATE TABLE IF NOT EXISTS reported_loan_reports(id INTEGER PRIMARY KEY,loan_id TEXT NOT NULL REFERENCES reported_loans(id),source TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(loan_id,source));
    ''')
    columns={r[1] for r in db.execute('PRAGMA table_info(reported_loans)')}
    for name,definition in (('issued_month','TEXT'),('estimated_maturity_month','TEXT'),('maturity_is_estimate','INTEGER NOT NULL DEFAULT 0'),('reported_total_balance','INTEGER'),('payment_is_estimate','INTEGER NOT NULL DEFAULT 0'),('product_type','TEXT')):
        if name not in columns:db.execute('ALTER TABLE reported_loans ADD COLUMN '+name+' '+definition)
    db.commit()


def save(ledger,debt_ref,name,monthly_payment,due_day,planning_from_period,reported_as_of,source,notes,account=None,source_line_name=None,source_line_amount=None,issued_month=None,term_months=None,reported_total_balance=None,payment_is_estimate=False,product_type=None):
    amount=money(monthly_payment)
    if amount<=0 or not isinstance(due_day,int) or not 1<=due_day<=31:raise ValueError('Нужны положительный платёж и день 1..31')
    if date.fromisoformat(planning_from_period+'-01').strftime('%Y-%m')!=planning_from_period:raise ValueError('Период: YYYY-MM')
    day(reported_as_of)
    if not debt_ref or not source:raise ValueError('Нужна отдельная идентичность долга и источник')
    if account and not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    maturity=None
    if issued_month is not None:
        issued=date.fromisoformat(issued_month+'-01')
        if issued.strftime('%Y-%m')!=issued_month:raise ValueError('Месяц выдачи: YYYY-MM; день не выдумывается')
    if term_months is not None and (not isinstance(term_months,int) or not 1<=term_months<=1200):raise ValueError('Срок: 1..1200 месяцев')
    if issued_month and term_months:
        serial=issued.year*12+issued.month-1+term_months
        maturity=f'{serial//12:04d}-{serial%12+1:02d}'
    total=money(reported_total_balance) if reported_total_balance is not None else None
    if total is not None and total<0:raise ValueError('Общая задолженность неотрицательна')
    with ledger.transaction():
        old=ledger.rows('SELECT * FROM reported_loans WHERE debt_ref=?',(debt_ref,))
        key=old[0]['id'] if old else ident()
        if old and ledger.rows('SELECT id FROM reported_loan_reports WHERE loan_id=? AND source=?',(key,source)):return {'id':key,'duplicate':True}
        ledger.db.execute('INSERT INTO reported_loans(id,debt_ref,name,monthly_payment,due_day,planning_from_period,account,principal_balance,annual_rate,term_months,payment_allocation,reported_as_of,source,notes,active,issued_month,estimated_maturity_month,maturity_is_estimate) VALUES(?,?,?,?,?,?,?,NULL,NULL,?,NULL,?,?,?,1,?,?,?) ON CONFLICT(debt_ref) DO UPDATE SET name=excluded.name,monthly_payment=excluded.monthly_payment,due_day=excluded.due_day,account=COALESCE(excluded.account,reported_loans.account),term_months=COALESCE(excluded.term_months,reported_loans.term_months),issued_month=COALESCE(excluded.issued_month,reported_loans.issued_month),estimated_maturity_month=COALESCE(excluded.estimated_maturity_month,reported_loans.estimated_maturity_month),maturity_is_estimate=MAX(excluded.maturity_is_estimate,reported_loans.maturity_is_estimate),reported_as_of=excluded.reported_as_of,source=excluded.source,notes=excluded.notes',
                          (key,debt_ref,name,amount,due_day,planning_from_period,account,term_months,reported_as_of,source,notes,issued_month,maturity,int(bool(maturity))))
        ledger.db.execute('UPDATE reported_loans SET reported_total_balance=?,payment_is_estimate=?,product_type=? WHERE id=?',(total,int(bool(payment_is_estimate)),product_type,key))
        payload={'debt_ref':debt_ref,'name':name,'monthly_payment':amount,'payment_is_estimate':bool(payment_is_estimate),'reported_total_balance':total,'product_type':product_type,'due_day':due_day,'planning_from_period':planning_from_period,'principal_balance':None,'annual_rate':None,'term_months':term_months,'issued_month':issued_month,'estimated_maturity_month':maturity,'maturity_is_estimate':bool(maturity),'payment_allocation':None,'notes':notes,'evidence':'user_reported'}
        ledger.db.execute('INSERT INTO reported_loan_reports(loan_id,source,payload) VALUES(?,?,?)',(key,source,json.dumps(payload,ensure_ascii=False)))
        # Match the original line by BOTH identity and its reported amount; a
        # card at the same bank is not this instalment loan.
        if source_line_name and source_line_amount:
            old_amount=money(source_line_amount)
            for rule in ledger.rows('SELECT * FROM rules'):
                if rule['name'].strip().casefold()==source_line_name.strip().casefold() and rule['amount']==old_amount:
                    ledger.db.execute('UPDATE rules SET end=? WHERE id=?',(reported_as_of,rule['id']))
            for plan in ledger.rows('SELECT * FROM plans WHERE cancelled=0'):
                if plan['name'].strip().casefold()==source_line_name.strip().casefold() and plan['amount']==old_amount:
                    ledger.db.execute('UPDATE plans SET cancelled=1 WHERE id=?',(plan['id'],))
        ledger.audit('reported_loan_save',{'loan_id':key,'source':source,'not_a_credit_card':True})
    return {'id':key,'duplicate':False}


def period_status(ledger,loan_id,period,status,source,payment_date=None,payment_account=None):
    loans=ledger.rows('SELECT * FROM reported_loans WHERE id=?',(loan_id,))
    if not loans:raise ValueError('Отдельный кредит не найден')
    if status not in ('unknown','external_paid','unpaid'):raise ValueError('Статус периода: unknown, external_paid или unpaid')
    first=date.fromisoformat(period+'-01');loan=loans[0]
    due=month_day(first.year,first.month,loan['due_day']).isoformat()
    if payment_date:day(payment_date)
    if payment_account and not ledger.rows('SELECT id FROM accounts WHERE id=?',(payment_account,)):raise ValueError('Счёт оплаты не найден')
    with ledger.transaction():
        ledger.db.execute('INSERT INTO reported_loan_periods VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(loan_id,period) DO UPDATE SET status=excluded.status,payment_date=excluded.payment_date,payment_account=excluded.payment_account,source=excluded.source',
                          (loan_id,period,due,loan['monthly_payment'],status,payment_date,payment_account,source))
        ledger.audit('reported_loan_period_status',{'loan_id':loan_id,'period':period,'status':status,'source':source,'cash_posting_created':False})
    return {'loan_id':loan_id,'period':period,'due_date':due,'status':status,'cash_posting_created':False,'payment_date':payment_date,'payment_account':payment_account}


def events(ledger,start,end,include_paid=False):
    result=[]
    for loan in ledger.rows('SELECT * FROM reported_loans WHERE active=1'):
        periods={r['period']:r for r in ledger.rows('SELECT * FROM reported_loan_periods WHERE loan_id=?',(loan['id'],))}
        cursor=date.fromisoformat(start).replace(day=1)
        candidates=[]
        while cursor.isoformat()<=end:
            if cursor.strftime('%Y-%m')>=loan['planning_from_period'] and (not loan['estimated_maturity_month'] or cursor.strftime('%Y-%m')<=loan['estimated_maturity_month']):
                when=month_day(cursor.year,cursor.month,loan['due_day']).isoformat()
                if start<=when<=end:candidates.append((when,periods.get(when[:7])))
            cursor=next_month(cursor)
        # Only explicitly recorded unknown/unpaid earlier periods cross the
        # horizon boundary; no fabricated history for older months.
        for record in periods.values():
            if record['due_date']<start and record['status'] in ('unknown','unpaid'):candidates.append((record['due_date'],record))
        for when,record in candidates:
            status=record['status'] if record else 'planned'
            if status=='external_paid' and not include_paid:continue
            result.append({'id':loan['id'],'reported_loan_id':loan['id'],'account':loan['account'],'kind':'expense','amount':record['amount'] if record else loan['monthly_payment'],
                           'date':when,'name':loan['name'],'lead_days':1,'occurrence':'reported-loan:'+loan['id']+':'+when,'period_status':status,
                           'status':'fulfilled_externally' if status=='external_paid' else status,'unpaid_confirmed':status=='unpaid','principal_reduction':None,'planning_basis':'user_reported_separate_loan','amount_is_estimate':bool(loan['payment_is_estimate']),'end_month_is_estimated':bool(loan['maturity_is_estimate'])})
    return result


def unresolved(ledger,as_of):
    return ledger.rows('SELECT p.loan_id,p.period,p.due_date,p.amount,p.status,l.name FROM reported_loan_periods p JOIN reported_loans l ON l.id=p.loan_id WHERE l.active=1 AND p.status=? AND p.due_date<=?',('unknown',as_of))


def bind(ledger,loan_id,account):
    if not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    with ledger.transaction():
        if not ledger.db.execute('UPDATE reported_loans SET account=? WHERE id=?',(account,loan_id)).rowcount:raise ValueError('Кредит не найден')
        ledger.audit('reported_loan_bind',{'loan_id':loan_id,'account':account})
    return {'loan_id':loan_id,'account':account}


def term_info(ledger,loan_id,as_of):
    rows=ledger.rows('SELECT * FROM reported_loans WHERE id=?',(loan_id,))
    if not rows:raise ValueError('Кредит не найден')
    loan=rows[0];current=date.fromisoformat(day(as_of))
    maturity=loan['estimated_maturity_month']
    if not maturity:return {'estimate_available':False,'principal_balance':loan['principal_balance'],'reason':'Неизвестен месяц выдачи/срок'}
    end=date.fromisoformat(maturity+'-01')
    months=max(0,(end.year-current.year)*12+end.month-current.month)
    candidates=events(ledger,(current.replace(day=1)).isoformat(),month_day(end.year,end.month,31).isoformat())
    future=[e for e in candidates if e['reported_loan_id']==loan_id and e['date']>as_of and e.get('period_status')!='unknown']
    return {'estimate_available':True,'issued_month':loan['issued_month'],'contract_term_months_reported':loan['term_months'],
            'estimated_maturity_month':maturity,'remaining_calendar_months_approx':months,'scenario_future_payment_count':len(future),
            'scenario_future_payments_total':sum(e['amount'] for e in future),'principal_balance':loan['principal_balance'],'annual_rate':loan['annual_rate'],
            'exact_issue_day':None,'exact_contract_last_due_date':None,'exact_remaining_payment_count':None,
            'assumption':'Оценка по сообщённым месяцу выдачи и сроку. Месячные даты — пользовательский план, не банковский график. Сумма будущих платежей не является остатком основного долга.'}


def schedule_from_document(ledger,loan_id,document_id,source):
    from .bank import validate
    loans=ledger.rows('SELECT * FROM reported_loans WHERE id=?',(loan_id,))
    if not loans:raise ValueError('Unknown loan')
    doc,rows=validate(ledger,document_id)
    if len({r['date'][:7] for r in rows})!=len(rows):raise ValueError('One payment per month required')
    key='document-schedule:'+document_id
    with ledger.transaction():
        old=ledger.rows('SELECT id FROM reported_loan_reports WHERE loan_id=? AND source=?',(loan_id,key))
        if old:return {'loan_id':loan_id,'duplicate':True}
        for r in rows:
            period=r['date'][:7]
            ledger.db.execute("INSERT INTO reported_loan_periods VALUES(?,?,?,?,?,NULL,NULL,?) ON CONFLICT(loan_id,period) DO UPDATE SET due_date=excluded.due_date,amount=excluded.amount",(loan_id,period,r['date'],r['amount'],'unknown' if r['date']<=local_today() else 'planned',source))
        end=max(r['date'] for r in rows)[:7]
        ledger.db.execute('UPDATE reported_loans SET estimated_maturity_month=?,maturity_is_estimate=0,notes=notes||? WHERE id=?',(end,'\nExact dates restored from '+document_id+'; owner date clarification '+source+'. Schedule totals are not current debt balance.',loan_id))
        ledger.db.execute('INSERT INTO reported_loan_reports(loan_id,source,payload) VALUES(?,?,?)',(loan_id,key,json.dumps({'document_id':document_id,'source':source,'rows':rows})))
        ledger.audit('reported_schedule_restored',{'loan_id':loan_id,'document_id':document_id,'source':source})
    return {'loan_id':loan_id,'payments':len(rows),'last_month':end,'duplicate':False}
