"""User-reported revolving/interest-only debt; no fabricated amortization."""
import json
from datetime import date
from .core import day,money,ident,month_day,next_month
from .bank import label


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS credit_cards(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,balance INTEGER,monthly_interest INTEGER,due_day INTEGER,first_due TEXT,account TEXT REFERENCES accounts(id),interest_only INTEGER,annual_rate TEXT,reported_as_of TEXT NOT NULL,source TEXT NOT NULL,notes TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,monthly_payment INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS credit_card_reports(id INTEGER PRIMARY KEY,card_id TEXT NOT NULL REFERENCES credit_cards(id),source TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(card_id,source));
    ''')
    columns={r[1]:r for r in db.execute('PRAGMA table_info(credit_cards)')}
    if 'monthly_payment' not in columns or columns['balance'][3] or columns['interest_only'][3]:
        # Preserve existing user reports while making UNKNOWN balance/allocation
        # genuinely NULL; zero is not a substitute for an unknown card debt.
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE credit_cards_v2(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,balance INTEGER,monthly_interest INTEGER,due_day INTEGER,first_due TEXT,account TEXT REFERENCES accounts(id),interest_only INTEGER,annual_rate TEXT,reported_as_of TEXT NOT NULL,source TEXT NOT NULL,notes TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,monthly_payment INTEGER NOT NULL)')
            payment='monthly_payment' if 'monthly_payment' in columns else 'monthly_interest'
            db.execute('INSERT INTO credit_cards_v2 SELECT id,name,balance,monthly_interest,due_day,first_due,account,interest_only,annual_rate,reported_as_of,source,notes,active,'+payment+' FROM credit_cards')
            db.execute('DROP TABLE credit_cards');db.execute('ALTER TABLE credit_cards_v2 RENAME TO credit_cards');db.commit()
        except BaseException:db.rollback();raise
        finally:db.execute('PRAGMA foreign_keys=ON')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Проверка связей после обновления карточной модели не прошла')


def save(ledger,name,balance,monthly_interest,reported_as_of,source,notes,due_day=None,first_due=None,account=None,monthly_payment=None,interest_only=True):
    balance=money(balance) if balance is not None else None
    interest=money(monthly_interest) if monthly_interest is not None else None
    payment=money(monthly_payment) if monthly_payment is not None else interest
    if (balance is not None and balance<0) or payment is None or payment<=0 or (interest is not None and interest<0):raise ValueError('Нужен известный положительный месячный платёж; долг/процентная часть могут быть неизвестны')
    if interest_only not in (True,False,None):raise ValueError('interest_only: true, false или null (неизвестно)')
    if interest_only is True and (interest is None or interest!=payment):raise ValueError('Для подтверждённых процентных платежей нужна точная процентная часть')
    day(reported_as_of)
    if not isinstance(source,str) or not source.strip():raise ValueError('Нужен источник сообщённых условий')
    if due_day is not None and (not isinstance(due_day,int) or not 1<=due_day<=31):raise ValueError('День платежа: 1..31')
    if first_due:day(first_due)
    if due_day and not first_due:raise ValueError('Нужна дата начала пользовательского месячного плана')
    if account and not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    payload={'name':name,'balance':balance,'monthly_interest':interest,'monthly_payment':payment,'due_day':due_day,'first_due':first_due,'reported_as_of':reported_as_of,'source':source,'notes':notes,'evidence':'user_reported_not_bank_verified','interest_only':interest_only,'annual_rate':None}
    with ledger.transaction():
        existing=ledger.rows('SELECT * FROM credit_cards WHERE name=?',(name,))
        key=existing[0]['id'] if existing else ident()
        if existing and ledger.rows('SELECT id FROM credit_card_reports WHERE card_id=? AND source=?',(key,source)):
            return {'id':key,'duplicate':True,'principal_unchanged_by_interest_payment':interest_only is True}
        ledger.db.execute('INSERT INTO credit_cards(id,name,balance,monthly_interest,due_day,first_due,account,interest_only,annual_rate,reported_as_of,source,notes,active,monthly_payment) VALUES(?,?,?,?,?,?,?,?,NULL,?,?,?,1,?) ON CONFLICT(name) DO UPDATE SET balance=excluded.balance,monthly_interest=excluded.monthly_interest,monthly_payment=excluded.monthly_payment,interest_only=excluded.interest_only,due_day=excluded.due_day,first_due=excluded.first_due,account=COALESCE(excluded.account,credit_cards.account),reported_as_of=excluded.reported_as_of,source=excluded.source,notes=excluded.notes',
                          (key,name,balance,interest,due_day,first_due,account,None if interest_only is None else int(interest_only),reported_as_of,source,notes,payment))
        ledger.db.execute('INSERT INTO credit_card_reports(card_id,source,payload) VALUES(?,?,?)',(key,source,json.dumps(payload,ensure_ascii=False)))
        for rule in ledger.rows('SELECT * FROM rules'):
            if label(rule['name'])==label(name):ledger.db.execute('UPDATE rules SET end=? WHERE id=?',(reported_as_of,rule['id']))
        for plan in ledger.rows('SELECT * FROM plans WHERE cancelled=0'):
            if label(plan['name'])==label(name) and plan['on_date']>=reported_as_of:ledger.db.execute('UPDATE plans SET cancelled=1 WHERE id=?',(plan['id'],))
        ledger.audit('credit_card_report',{'card_id':key,'source':source,'interest_only':interest_only,'history_is_note_only':True})
    return {'id':key,'duplicate':False,'principal_unchanged_by_interest_payment':interest_only is True,'bank_tariff_verified':False}


def events(ledger,start,end):
    result=[];cursor=date.fromisoformat(start).replace(day=1)
    while cursor.isoformat()<=end:
        for card in ledger.rows('SELECT * FROM credit_cards WHERE active=1'):
            when=month_day(cursor.year,cursor.month,card['due_day']).isoformat() if card['due_day'] else max(cursor,date.fromisoformat(card['reported_as_of'])).isoformat()
            first=card['first_due'] or card['reported_as_of']
            if when[:7]!=cursor.strftime('%Y-%m') or not start<=when<=end or when<first:continue
            result.append({'id':card['id'],'card_id':card['id'],'account':card['account'],'kind':'expense','amount':card['monthly_payment'],'date':when,'name':card['name']+(' — проценты по карте (условия со слов пользователя)' if card['interest_only']==1 else ' — обязательный платёж по карте (распределение неизвестно)'),
                           'lead_days':1,'undated':card['due_day'] is None,'occurrence':'card:'+card['id']+':'+when,'principal_reduction':0 if card['interest_only']==1 else None,'planning_basis':'user_configured_card_payment','bank_tariff_verified':False})
        cursor=next_month(cursor)
    return result


def scenario(ledger,card_id,extra_principal='0.00',months=12):
    rows=ledger.rows('SELECT * FROM credit_cards WHERE id=?',(card_id,))
    if not rows:raise ValueError('Карта не найдена')
    card=rows[0];extra=money(extra_principal)
    if extra<0 or not isinstance(months,int) or not 1<=months<=1200:raise ValueError('Некорректный горизонт/доплата в основной долг')
    if card['balance'] is None or card['interest_only']!=1 or card['monthly_interest'] is None:
        return {'status':'insufficient_terms','unit':'kopecks','starting_balance':card['balance'],'ending_balance':None,'principal_paid':None,'payoff_months':None,'annual_rate':None,'historical_interest_total':None,'monthly_payment':card['monthly_payment'],
                'explanation':'Месячный платёж сохранён в бюджете. Для стратегии погашения этой карты ещё неизвестны остаток и/или распределение платежа на проценты и тело; условия другой карты не копируются. Срок погашения не выдумывается.'}
    balance=card['balance'];interest=0;principal=0;schedule=[]
    for month in range(1,months+1):
        if balance<=0:break
        reduction=min(balance,extra)
        balance-=reduction;principal+=reduction;interest+=card['monthly_interest']
        schedule.append({'month':month,'interest':card['monthly_interest'],'principal':reduction,'balance':balance})
    payoff_months=(card['balance']+extra-1)//extra if extra else None
    return {'status':'ok','unit':'kopecks','starting_balance':card['balance'],'ending_balance':balance,'interest_scenario':interest,'principal_paid':principal,
            'payoff_months':payoff_months,'annual_rate':None,'historical_interest_total':None,'schedule':schedule,
            'assumption':'Условия со слов пользователя. Без доплаты тело долга не уменьшается и срока погашения нет. Сценарий условно сохраняет текущую месячную процентную нагрузку; ставка неизвестна. Доплата уменьшает тело только если банк действительно направляет её в основной долг. Это не банковский график и не расчёт прошлых шести лет.'}


def bind(ledger,card_id,account):
    if not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    with ledger.transaction():
        if not ledger.db.execute('UPDATE credit_cards SET account=? WHERE id=?',(account,card_id)).rowcount:raise ValueError('Карта не найдена')
        ledger.audit('credit_card_bind',{'card_id':card_id,'account':account})
    return {'card_id':card_id,'account':account}
