"""Earmarks, not cash postings. All returned monetary values are kopecks."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from .core import day, money, rub, month_day


def setup(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS savings_goals(ref TEXT PRIMARY KEY,name TEXT NOT NULL,plan TEXT NOT NULL,status TEXT NOT NULL,saved INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS savings_changes(source TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS savings_previews(plan TEXT PRIMARY KEY,source TEXT NOT NULL);
    """)


def today():
    return datetime.now(timezone(timedelta(hours=5))).date().isoformat()


def calculate(name, items, deadline, first_date, monthday):
    deadline, first_date = day(deadline), day(first_date)
    if not name.strip() or not items or not isinstance(items, list):
        raise ValueError('Нужны название цели и товары с названием, получателем и ценой')
    if type(monthday) is not int or not 1 <= monthday <= 31:
        raise ValueError('День взноса должен быть 1..31')
    if deadline < first_date or (date.fromisoformat(deadline)-date.fromisoformat(first_date)).days > 3660:
        raise ValueError('Срок цели раньше первого взноса или больше 10 лет')
    parsed = []
    for item in items:
        value = money(item['price'])
        if value <= 0 or not item.get('name'):
            raise ValueError('Укажите товар и положительную цену')
        parsed.append({'name':item['name'], 'recipient':item.get('recipient',''), 'amount':value})
    cursor = date.fromisoformat(first_date)
    dates = [first_date]
    while True:
        month = cursor.month % 12 + 1
        cursor = month_day(cursor.year + (cursor.month == 12), month, monthday)
        if cursor.isoformat() > deadline:break
        dates.append(cursor.isoformat())
    target = sum(item['amount'] for item in parsed)
    base, remainder = divmod(target, len(dates))
    return {'name':name,'items':parsed,'target':target,'deadline':deadline,
            'installments':[{'date':when,'amount':base+(index < remainder)} for index,when in enumerate(dates)],
            'monthday':monthday,'unit':'kopecks','is_actual_expense':False}


def view(ledger, ref=None):
    rows = ledger.rows('SELECT * FROM savings_goals WHERE ref=?',(ref,)) if ref else ledger.rows('SELECT * FROM savings_goals')
    return {'unit':'kopecks','proposals':[json.loads(r['plan']) for r in ledger.rows('SELECT plan FROM savings_previews')], 'goals':[
        {**r,'plan':json.loads(r['plan']),'remaining':max(0,json.loads(r['plan'])['target']-r['saved'])}
        for r in rows]}


def outstanding(plan, saved):
    for installment in plan['installments']:
        covered = min(saved, installment['amount'])
        saved -= covered
        if installment['amount'] > covered:
            yield {**installment,'amount':installment['amount']-covered}


def holds(ledger, as_of, until):
    result = []
    for goal in view(ledger)['goals']:
        if goal['status'] in ('cancelled','released'):continue
        common = {'kind':'expense','budget_hold':True,'savings_goal':goal['ref'],
                  'name':'Накопления: '+goal['name'],'is_actual_expense':False}
        if goal['saved']:
            result.append({**common,'date':as_of,'amount':goal['saved'],'status':'already_earmarked'})
        if goal['status']=='active':
            for installment in outstanding(goal['plan'],goal['saved']):
                if installment['date']<=until:
                    result.append({**common,**installment,'date':max(as_of,installment['date']),'status':'planned_saving'})
    return result


def preview(ledger, name, items, deadline, first_date, monthday, as_of=None, source='local-preview', **forecast_parameters):
    from .planning import forecast
    as_of = day(as_of or today())
    if first_date < as_of:raise ValueError('Первый взнос не может быть в прошлом')
    plan = calculate(name,items,deadline,first_date,monthday)
    budget = forecast(ledger,as_of,deadline,**forecast_parameters)
    available = budget['actual_balance']
    minimum = available
    gap = as_of if available is not None and available < (budget['reserve'] or 0) else None
    for row in budget['days']:
        if available is None:continue
        events=row['events']+[dict(i,kind='expense') for i in plan['installments'] if i['date']==row['date']]
        for event in sorted(events,key=lambda e:e['kind']=='income'):
            available += event['amount']*(1 if event['kind']=='income' else -1)
            minimum=min(minimum,available)
            if gap is None and available < (budget['reserve'] or 0):gap=row['date']
    with ledger.transaction():
        ledger.db.execute('INSERT INTO savings_previews VALUES(?,?) ON CONFLICT(plan) DO NOTHING',(json.dumps(plan,ensure_ascii=False,sort_keys=True),source))
    return {'plan':plan,'saved':False,'requires_plan_agreement':True,
            'affordable':not budget['missing'] and gap is None,
            'first_gap':gap,'minimum_after_savings':minimum,
            'missing':budget['missing'],'actual_balance':budget['actual_balance'],
            'assumptions':budget['assumptions']}


def save(ledger, ref, name, items, deadline, first_date, monthday, source):
    plan = calculate(name,items,deadline,first_date,monthday)
    if not ref or not source:raise ValueError('Нужны ref цели и источник согласования')
    with ledger.transaction():
        proposal=ledger.rows('SELECT source FROM savings_previews WHERE plan=?',(json.dumps(plan,ensure_ascii=False,sort_keys=True),))
        if not proposal or source==proposal[0]['source'] or source.startswith('owner-change:'+proposal[0]['source']+':'):
            raise ValueError('Сначала покажите расчёт владельцу, затем сохраните тот же план по его согласию в следующем сообщении. Код подтверждения не нужен.')
        old = ledger.rows('SELECT * FROM savings_goals WHERE ref=?',(ref,))
        if old:raise ValueError('Цель уже существует: не создавайте её повторно; для нового графика отмените старый план и учтите накопленное')
        for existing in view(ledger)['goals']:
            if existing['status'] not in ('cancelled','released') and (
                    existing['name'].casefold()==name.casefold() or existing['plan']==plan):
                raise ValueError('Такая цель уже есть под другим ref; используйте существующую, не резервируйте деньги дважды')
        ledger.db.execute("INSERT INTO savings_goals VALUES(?,?,?,'active',0,?)",
                          (ref,name,json.dumps(plan,ensure_ascii=False),source))
        ledger.audit('savings_plan_agreed',{'ref':ref,'source':source,'plan':plan})
    return {'ref':ref,'plan':plan,'status':'active','cash_posting_created':False}


def progress(ledger, ref, saved_total, as_of, source):
    """Owner reports TOTAL currently earmarked, avoiding duplicate incremental top-ups."""
    value, as_of = money(saved_total), day(as_of)
    if value < 0 or as_of > today():raise ValueError('Нужен неотрицательный фактический остаток накоплений на сегодня или ранее')
    payload=json.dumps([ref,value,as_of])
    with ledger.transaction():
        previous=ledger.rows('SELECT * FROM savings_changes WHERE source=?',(source,))
        if previous:
            if previous[0]['payload']!=payload:raise ValueError('Источник уже использован')
            return {**json.loads(previous[0]['result']),'duplicate':True}
        found=view(ledger,ref)['goals']
        if not found or found[0]['status'] in ('released','cancelled'):raise ValueError('Активная цель не найдена')
        goal=found[0]
        status='completed' if value>=goal['plan']['target'] else ('paused' if goal['status']=='paused' else 'active')
        ledger.db.execute('UPDATE savings_goals SET saved=?,status=? WHERE ref=?',(value,status,ref))
        result={'ref':ref,'saved':value,'remaining':max(0,goal['plan']['target']-value),'status':status,'cash_posting_created':False}
        ledger.db.execute('INSERT INTO savings_changes VALUES(?,?,?)',(source,payload,json.dumps(result)))
        ledger.audit('savings_progress',{'source':source,'as_of':as_of,**result})
        return result


def status(ledger, ref, status, source):
    if status not in ('active','paused','cancelled','released'):raise ValueError('Неизвестный статус цели')
    with ledger.transaction():
        if not view(ledger,ref)['goals']:raise ValueError('Цель не найдена')
        ledger.db.execute('UPDATE savings_goals SET status=? WHERE ref=?',(status,ref))
        ledger.audit('savings_status',{'ref':ref,'status':status,'source':source})
    return {'ref':ref,'status':status,'cash_posting_created':False}


def reminder_events(ledger, until, as_of=None):
    events=[]
    for goal in view(ledger)['goals']:
        if goal['status']!='active':continue
        # One oldest unfinished installment per goal; later months don't create a flood.
        pending=list(outstanding(goal['plan'],goal['saved']))
        if pending and pending[0]['date']<=until:
            item=pending[0]
            overdue=(date.fromisoformat(as_of or today())-date.fromisoformat(item['date'])).days
            if overdue>=7:
                item={**item,'date':(date.fromisoformat(item['date'])+timedelta(days=(overdue//7)*7)).isoformat()}
            key=hashlib.sha256(goal['ref'].encode()).hexdigest()[:20]
            events.append({**item,'name':f"Отложить на «{goal['name']}» (накоплено {rub(goal['saved'])} из {rub(goal['plan']['target'])} RUB)",
                           'occurrence':f"savings:{key}:{item['date']}",'lead_days':1})
    return events
