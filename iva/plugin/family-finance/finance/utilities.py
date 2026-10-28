"""Variable monthly bills; one identity and one overriding quote per month."""
import json
from datetime import date
from .core import money, day, ident, month_day, next_month


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS utility_bills(id TEXT PRIMARY KEY,bill_ref TEXT UNIQUE NOT NULL,name TEXT NOT NULL,due_day INTEGER,start_period TEXT NOT NULL,account TEXT REFERENCES accounts(id),policy TEXT NOT NULL,notes TEXT NOT NULL,source TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS utility_quotes(bill_id TEXT NOT NULL REFERENCES utility_bills(id),period TEXT NOT NULL,amount INTEGER NOT NULL,source TEXT NOT NULL,PRIMARY KEY(bill_id,period));
    CREATE TABLE IF NOT EXISTS utility_periods(bill_id TEXT NOT NULL REFERENCES utility_bills(id),period TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(bill_id,period));
    ''')
    db.commit()


def save(ledger,bill_ref,name,start_period,policy,notes,source,due_day=None,account=None):
    date.fromisoformat(start_period+'-01')
    if due_day is not None and (not isinstance(due_day,int) or not 1<=due_day<=31):raise ValueError('День оплаты: 1..31 или null')
    if account and not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    if money(policy['baseline'])<=0:raise ValueError('Нужен положительный ориентир')
    for key in ('warm_range','cold_typical_range','cold_severe_range','overall_range'):
        if key in policy:
            low,high=map(money,policy[key])
            if not 0<=low<=high:raise ValueError('Некорректный диапазон')
    with ledger.transaction():
        existing=ledger.rows('SELECT id FROM utility_bills WHERE bill_ref=?',(bill_ref,))
        key=existing[0]['id'] if existing else ident()
        ledger.db.execute('INSERT INTO utility_bills VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(bill_ref) DO UPDATE SET name=excluded.name,due_day=excluded.due_day,policy=excluded.policy,notes=excluded.notes,source=excluded.source,account=COALESCE(excluded.account,utility_bills.account)',(key,bill_ref,name,due_day,start_period,account,json.dumps(policy,ensure_ascii=False),notes,source))
        ledger.audit('utility_save',{'bill_id':key,'source':source,'cash_posting_created':False})
    return {'id':key}


def quote(ledger,bill_id,period,amount,source):
    date.fromisoformat(period+'-01');value=money(amount)
    if value<=0:raise ValueError('Сумма должна быть положительной')
    with ledger.transaction():
        ledger.db.execute('INSERT INTO utility_quotes VALUES(?,?,?,?) ON CONFLICT(bill_id,period) DO UPDATE SET amount=excluded.amount,source=excluded.source',(bill_id,period,value,source))
        ledger.audit('utility_quote',{'bill_id':bill_id,'period':period,'source':source,'replaces_estimate':True,'cash_posting_created':False})
    return {'bill_id':bill_id,'period':period,'cash_posting_created':False}


def bind(ledger,bill_id,account):
    if not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    with ledger.transaction():
        if not ledger.db.execute('UPDATE utility_bills SET account=? WHERE id=?',(account,bill_id)).rowcount:raise ValueError('Расход не найден')
    return {'bill_id':bill_id,'account':account}


def period_status(ledger,bill_id,period,status,source):
    date.fromisoformat(period+'-01')
    if status not in ('unknown','external_paid','unpaid'):raise ValueError('Неизвестный статус периода')
    with ledger.transaction():
        ledger.db.execute('INSERT INTO utility_periods VALUES(?,?,?,?) ON CONFLICT(bill_id,period) DO UPDATE SET status=excluded.status,source=excluded.source',(bill_id,period,status,source))
        ledger.audit('utility_status',{'bill_id':bill_id,'period':period,'status':status,'source':source,'cash_posting_created':False})
    return {'bill_id':bill_id,'period':period,'status':status,'cash_posting_created':False}


def unresolved(ledger,as_of):
    return [{'bill_id':e['utility_bill_id'],'name':e['name'],'period':e['date'][:7],'due_date':e['date'],'amount':e['amount'],'status':'unknown'} for e in events(ledger,as_of[:7]+'-01',as_of) if e.get('period_status')=='unknown']


def events(ledger,start,end):
    result=[]
    for bill in ledger.rows('SELECT * FROM utility_bills'):
        policy=json.loads(bill['policy']);quotes={r['period']:r for r in ledger.rows('SELECT * FROM utility_quotes WHERE bill_id=?',(bill['id'],))}
        statuses={r['period']:r['status'] for r in ledger.rows('SELECT * FROM utility_periods WHERE bill_id=?',(bill['id'],))}
        cursor=date.fromisoformat(min([start]+[p+'-01' for p,s in statuses.items() if s in ('unknown','unpaid')])).replace(day=1)
        while cursor.isoformat()<=end:
            period=cursor.strftime('%Y-%m')
            when=month_day(cursor.year,cursor.month,bill['due_day']).isoformat() if bill['due_day'] else max(cursor,date.fromisoformat(start)).isoformat()
            status=statuses.get(period,'planned')
            if period>=bill['start_period'] and when<=end and (when>=start or status in ('unknown','unpaid')) and status!='external_paid':
                override=quotes.get(period);estimated=not override and bool(policy.get('variable'))
                event={'id':bill['id'],'utility_bill_id':bill['id'],'kind':'expense','name':bill['name'],'date':when,'account':bill['account'],'amount':override['amount'] if override else money(policy['baseline']),'undated':bill['due_day'] is None,'lead_days':1,'occurrence':'utility:'+bill['id']+':'+period,'amount_is_estimate':estimated,'planning_basis':'monthly_quote' if override else 'user_reported_planning_baseline','season_assignment':None,'notes':bill['notes']}
                if estimated:
                    event['estimated_range']=[money(v) for v in policy['overall_range']]
                    event['seasonal_policy']=policy
                event['period_status']=status
                event['unpaid_confirmed']=status=='unpaid'
                result.append(event)
            cursor=next_month(cursor)
    return result
