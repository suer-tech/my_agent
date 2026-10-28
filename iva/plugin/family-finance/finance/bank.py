"""Exact bank schedules, immutable sources and versioned planned payments."""
import json
import re
from .core import day,money,ident


def label(value):return re.sub(r'[^а-яёa-z0-9]+','',str(value).casefold().replace('банк','').replace('кредит',''))


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS source_documents(id TEXT PRIMARY KEY,path TEXT NOT NULL,kind TEXT NOT NULL,pages INTEGER NOT NULL,extraction TEXT NOT NULL,status TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bank_schedules(id TEXT PRIMARY KEY,loan_name TEXT NOT NULL,document_id TEXT NOT NULL REFERENCES source_documents(id),account TEXT REFERENCES accounts(id),active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL,UNIQUE(loan_name,document_id));
    CREATE TABLE IF NOT EXISTS bank_payments(id TEXT PRIMARY KEY,schedule_id TEXT NOT NULL REFERENCES bank_schedules(id),on_date TEXT NOT NULL,amount INTEGER NOT NULL,principal INTEGER,interest INTEGER,fees INTEGER,balance INTEGER,page INTEGER NOT NULL,row_ref TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_document_revisions(id INTEGER PRIMARY KEY,document_id TEXT NOT NULL,source TEXT NOT NULL,payload TEXT NOT NULL);
    ''')
    if 'balance_before' not in {r[1] for r in db.execute('PRAGMA table_info(bank_payments)')}:
        db.execute('ALTER TABLE bank_payments ADD COLUMN balance_before INTEGER');db.commit()
    if 'unpaid_confirmed' not in {r[1] for r in db.execute('PRAGMA table_info(bank_payments)')}:
        db.execute('ALTER TABLE bank_payments ADD COLUMN unpaid_confirmed INTEGER NOT NULL DEFAULT 0');db.commit()


def validate(ledger,document_id):
    docs=ledger.rows('SELECT * FROM source_documents WHERE id=?',(document_id,))
    if not docs:raise ValueError('Исходный документ не найден')
    doc=docs[0];data=json.loads(doc['extraction'])
    if data.get('questions'):raise ValueError('В графике есть неоднозначные строки. Сначала нужно уточнение/читаемый документ.')
    if data.get('currency')!='RUB':raise ValueError('График должен быть в RUB; валюта не выдумывается')
    if sorted(set(data.get('pages_covered',[])))!=list(range(1,doc['pages']+1)):raise ValueError('Не подтверждено извлечение всех страниц графика')
    rows=data.get('rows',[])
    if not rows:raise ValueError('Платёжные строки не найдены')
    if data.get('candidate_row_count') and len(rows)!=data['candidate_row_count']:raise ValueError('Число извлечённых платежей не совпало со строками исходного PDF; требуется сверка всех строк')
    seen=set();result=[]
    for row in rows:
        when=day(row['date']);amount=money(row['payment'])
        if amount<0 or not 1<=row['page']<=doc['pages'] or not row['row_ref']:raise ValueError('Неполная строка графика')
        parts={key:money(row[key]) if row.get(key) is not None else None for key in ('principal','interest','fees','balance','balance_before')}
        if any(v is not None and v<0 for v in parts.values()):raise ValueError('Отрицательный компонент в графике')
        if all(parts[k] is not None for k in ('principal','interest','fees')) and sum(parts[k] for k in ('principal','interest','fees'))!=amount:raise ValueError('Основной долг + проценты + комиссии не равны платежу')
        if parts['principal'] is not None and parts['interest'] is not None and parts['fees'] is None and parts['principal']+parts['interest']!=amount:raise ValueError('Неясна разница между платежом и суммой долга/процентов; комиссия не выдумывается')
        key=(row.get('payment_no'),when,amount,tuple(parts.values()))
        if key in seen:raise ValueError('Повторная платёжная строка: требуется сверка, чтобы не задвоить график')
        seen.add(key);result.append({'date':when,'amount':amount,**parts,'page':row['page'],'row_ref':str(row['row_ref']),'unpaid_confirmed':row.get('unpaid_confirmed') is True})
    return doc,sorted(result,key=lambda r:(r['date'],r['row_ref']))


def import_schedule(ledger,document_id,loan_name,account=None):
    doc,rows=validate(ledger,document_id)
    if account and not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    with ledger.transaction():
        previous=ledger.rows('SELECT * FROM bank_schedules WHERE loan_name=? AND document_id=?',(loan_name,document_id))
        if previous:return {'id':previous[0]['id'],'duplicate':True,'payments':len(rows)}
        matching=[r for r in ledger.rows('SELECT * FROM rules') if label(r['name'])==label(loan_name)]
        if len(matching)>1:raise ValueError('У банка несколько похожих обязательств. Требуется уточнить договор; ни одно не заменено.')
        versions=ledger.rows('SELECT MAX(version) AS n FROM bank_schedules WHERE loan_name=?',(loan_name,))
        version=(versions[0]['n'] or 0)+1;key=ident()
        ledger.db.execute('UPDATE bank_schedules SET active=0 WHERE loan_name=?',(loan_name,))
        ledger.db.execute('INSERT INTO bank_schedules VALUES(?,?,?,?,1,?)',(key,loan_name,document_id,account,version))
        for row in rows:
            ledger.db.execute('INSERT INTO bank_payments(id,schedule_id,on_date,amount,principal,interest,fees,balance,page,row_ref,balance_before,unpaid_confirmed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(ident(),key,row['date'],row['amount'],row['principal'],row['interest'],row['fees'],row['balance'],row['page'],row['row_ref'],row['balance_before'],int(row['unpaid_confirmed'])))
        for rule in matching:ledger.db.execute('UPDATE rules SET end=? WHERE id=?',('0001-01-01',rule['id']))
        ledger.db.execute("UPDATE source_documents SET status='imported' WHERE id=?",(document_id,))
        ledger.audit('bank_schedule_import',{'schedule':key,'document':document_id,'version':version,'payments':len(rows),'replaced_rules':[r['id'] for r in matching]})
    return {'id':key,'duplicate':False,'payments':len(rows),'version':version,'account_needed':account is None}


def bind(ledger,schedule_id,account):
    if not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    with ledger.transaction():
        if not ledger.db.execute('UPDATE bank_schedules SET account=? WHERE id=?',(account,schedule_id)).rowcount:raise ValueError('График не найден')
        ledger.audit('bank_schedule_bind',{'schedule':schedule_id,'account':account})
    return {'schedule':schedule_id,'account':account}


def amend(ledger,document_id,resolutions,remaining_questions,source):
    docs=ledger.rows('SELECT * FROM source_documents WHERE id=?',(document_id,))
    if not docs:raise ValueError('Документ не найден')
    if docs[0]['status']=='imported':raise ValueError('Подтверждённый график неизменяем. Для новой банковской версии нужен новый документ.')
    data=json.loads(docs[0]['extraction'])
    if not isinstance(resolutions,list) or not isinstance(remaining_questions,list) or not all(isinstance(q,str) for q in remaining_questions):raise ValueError('Нужен список уточнений строк и оставшихся вопросов')
    original=json.dumps(data,ensure_ascii=False)
    for change in resolutions:
        matches=[r for r in data.get('rows',[]) if (change.get('payment_no') is not None and r.get('payment_no')==change['payment_no']) or (change.get('row_ref') and r['row_ref']==change['row_ref'])]
        if len(matches)!=1:raise ValueError('Уточнение должно указывать одну конкретную строку графика')
        row=matches[0]
        for field,value in change.items():
            if field in ('payment_no','row_ref'):continue
            if field not in ('date','payment','principal','interest','fees','balance','balance_before'):raise ValueError('Неизвестное уточняемое поле')
            if field=='date':day(value)
            elif money(value)<0:raise ValueError('Уточнённая сумма должна быть неотрицательной')
            row[field]=value
        row.setdefault('clarifications',[]).append({'source':source,'fields':list(change)})
        row['row_ref']+=' | user:'+source
    data['questions']=list(remaining_questions)
    if any(r.get('date') is None or r.get('payment') is None for r in data.get('rows',[])):data['questions'].append('Ещё остались строки без точной даты или полной суммы.')
    with ledger.transaction():
        ledger.db.execute('INSERT INTO source_document_revisions(document_id,source,payload) VALUES(?,?,?)',(document_id,source,original))
        ledger.db.execute("UPDATE source_documents SET extraction=?,status='clarified' WHERE id=?",(json.dumps(data,ensure_ascii=False),document_id))
    return data
