"""Persistent incomplete intake and atomic, idempotent confirmation of monthly lists."""
import json
import re
from finance.core import money,rub

ALLOWED = {'schedule','loan','account','configure'}


def selected_basis(text):
    if text in ('/basis lines','/basis total'):return text.split()[1]
    if re.search(r'(считать|считай|верны|используй|берём|берем)(\s+верными)?\s+(отдельные\s+)?строки',text,re.I):return 'lines'
    if re.search(r'(считать|считай|верны|используй|берём|берем)(\s+верными)?\s+(указанные\s+)?итоги',text,re.I):return 'total'
    return None


def setup(ledger,chat):
    ledger.db.executescript('''
    CREATE TABLE IF NOT EXISTS local_intake(chat INTEGER PRIMARY KEY,payload TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS local_intake_sources(source TEXT PRIMARY KEY,chat INTEGER NOT NULL,text TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS local_batches(source TEXT PRIMARY KEY,result TEXT NOT NULL);
    ''')
    if not ledger.rows("SELECT value FROM local_meta WHERE key='intake_bootstrap_v2'"):
        with ledger.transaction():
            for row in ledger.rows('SELECT * FROM local_updates WHERE chat=? ORDER BY id',(chat,)):
                meta=json.loads(row.get('metadata') or '{}')
                text=meta.get('transcript',row['text'])
                if re.search('ежемесяч|планируем|руб|₽|кредит|ипотек|альф|банк|плат[её]ж|расход',text,re.I) or (len(text.splitlines())>2 and len(re.findall(r'\d',text))>3):
                    ledger.db.execute('INSERT OR IGNORE INTO local_intake_sources VALUES(?,?,?)',(f"telegram:{chat}:{row['id']}:0",chat,text))
                basis=selected_basis(text)
                if basis:choose(ledger,chat,basis)
            ledger.db.execute("INSERT INTO local_meta VALUES('intake_bootstrap_v2','done')")


def capture(ledger,chat,text,source):
    current=context(ledger,chat)
    if re.search('ежемесяч|планируем',text,re.I) or current:
        with ledger.transaction():
            ledger.db.execute('INSERT OR IGNORE INTO local_intake_sources VALUES(?,?,?)',(source,chat,text[:20000]))


def context(ledger,chat):
    rows=ledger.rows('SELECT payload FROM local_intake WHERE chat=?',(chat,))
    source=ledger.rows('SELECT text FROM local_intake_sources WHERE chat=? ORDER BY rowid DESC LIMIT 30',(chat,))
    if not rows and not source:return {}
    return {'draft':json.loads(rows[0]['payload']) if rows else {},'original_messages':[r['text'] for r in reversed(source)]}


def save(ledger,chat,payload):
    if not isinstance(payload.get('items'),list) or len(payload['items'])>60: raise ValueError('Черновик списка: items — до 60 пунктов')
    questions=payload.get('questions',[])
    if not isinstance(questions,list) or not all(isinstance(q,str) for q in questions): raise ValueError('questions — список уточнений')
    previous=context(ledger,chat).get('draft',{})
    payload=dict(payload)
    payload.pop('_basis_choice',None)
    for key in ('lines','category_totals'):
        if key not in payload and key in previous:payload[key]=previous[key]
    if previous.get('_basis_choice'):
        payload['_basis_choice']=previous['_basis_choice']
    comparison=reconcile(payload)
    encoded=json.dumps(payload,ensure_ascii=False)
    if len(encoded)>120000:raise ValueError('Список слишком длинный')
    with ledger.transaction():ledger.db.execute('INSERT OR REPLACE INTO local_intake VALUES(?,?)',(chat,encoded))
    issues=[row for row in comparison if not row['matches']]
    discrepancy=''
    if issues and not payload.get('_basis_choice'):
        discrepancy='\nСтроки и итоги не совпадают или неполны. Они сохранены раздельно и НЕ складываются друг с другом. Выберите достоверный источник: /basis lines — отдельные строки; /basis total — указанные итоги.'
    followup='' if discrepancy else ('\nСледующее уточнение: '+questions[0] if questions else '\nПодготовлю сводное предложение для подтверждения.')
    return 'Сохранил черновик списка: '+str(len(payload['items']))+' пунктов. В баланс и обязательства ещё ничего не записано.\n'+str(payload.get('summary',''))[:1000]+discrepancy+followup


def reconcile(payload):
    """Details and stated totals are separate evidence, never additive expenses."""
    lines=payload.get('lines',[]);totals=payload.get('category_totals',[])
    sums={};incomplete=set()
    for line in lines:
        category=str(line.get('category',''))
        try:sums[category]=sums.get(category,0)+money(line.get('amount'))
        except ValueError:incomplete.add(category)
    result=[]
    for total in totals:
        category=str(total.get('category',''))
        stated=money(total.get('amount'))
        details=sums.get(category)
        result.append({'category':category,'line_sum':details,'stated_total':stated,'matches':details==stated and category not in incomplete})
    return result


def choose(ledger,chat,basis):
    if basis not in ('lines','total'):raise ValueError('Выберите /basis lines или /basis total')
    current=context(ledger,chat).get('draft',{})
    if not current:current={'items':[],'questions':[]}
    current['_basis_choice']=basis
    with ledger.transaction():ledger.db.execute('INSERT OR REPLACE INTO local_intake VALUES(?,?)',(chat,json.dumps(current,ensure_ascii=False)))


def guard(ledger,chat,payload):
    draft=context(ledger,chat).get('draft',{})
    comparison=reconcile(draft)
    if any(not row['matches'] for row in comparison) and not draft.get('_basis_choice'):
        raise ValueError('Строки и указанные итоги расходятся. Сначала ответьте /basis lines или /basis total; суммы не выбираются автоматически.')
    grouped={}
    for item in payload['items']:
        if item['action']!='schedule' or item['payload'].get('kind')!='expense':continue
        category=item.get('category')
        if category:
            group=grouped.setdefault(category,{'sum':0,'roles':set()})
            group['sum']+=money(item['payload']['amount']);group['roles'].add(item.get('role','line'))
    for category,group in grouped.items():
        if {'line','aggregate'}<=group['roles']:raise ValueError('Нельзя складывать итог категории и её отдельные строки: '+category)
    for row in comparison:
        if row['line_sum'] is None:continue
        if row['category'] not in grouped:
            if draft.get('_basis_choice')=='lines':continue
            raise ValueError('Нужна категория каждого платежа для сверки строк с итогом: '+row['category'])
        expected=row['stated_total'] if draft.get('_basis_choice')=='total' else row['line_sum']
        named=[line for line in draft.get('lines',[]) if line.get('category')==row['category'] and line.get('name')]
        if named and draft.get('_basis_choice')=='lines':
            for item in payload['items']:
                if item['action']!='schedule' or item.get('category')!=row['category']:continue
                if item.get('role')=='aggregate':raise ValueError('Пользователь выбрал строки, а не агрегированный расход категории')
                found=[line for line in named if str(line['name']).strip().casefold()==str(item['payload'].get('name','')).strip().casefold()]
                if not found or not any(money(line['amount'])==money(item['payload']['amount']) for line in found):raise ValueError('Платёж не соответствует сохранённой детальной строке: '+str(item['payload'].get('name','')))
        elif grouped[row['category']]['sum']!=expected:raise ValueError('Сумма планов не соответствует выбранному источнику: '+row['category'])


def mark_bank(ledger,chat,loan_name,schedule_id):
    from finance.bank import label
    current=context(ledger,chat).get('draft',{})
    for collection in ('items','lines'):
        for item in current.get(collection,[]):
            name=item.get('name',item.get('payload',{}).get('name',''))
            explicit=re.match(r'^(\d+(?:[.,]\d{1,2})?)\s+(.+)$',loan_name)
            match=label(name)==label(loan_name)
            if explicit:
                amount=item.get('original_approximate_amount',item.get('amount'))
                match=label(name)==label(explicit[2]) and amount is not None and money(str(amount))==money(explicit[1])
            if match:item['bank_schedule_id']=schedule_id;item['do_not_create_approximate_payment']=True
    if current:
        with ledger.transaction():ledger.db.execute('INSERT OR REPLACE INTO local_intake VALUES(?,?)',(chat,json.dumps(current,ensure_ascii=False)))


def normalize(payload):
    items=payload.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=40:raise ValueError('Подтверждение списка: 1..40 пунктов за раз')
    for item in items:
        if not isinstance(item,dict) or not {'action','payload'}<=set(item) or set(item)-{'action','payload','category','role','existing_id','distinct'} or item['action'] not in ALLOWED or not isinstance(item['payload'],dict):
            raise ValueError('В списке разрешены только планы, условия кредитов, счета и настройки. Фактических расходов здесь нет.')
    return items


def apply(ledger,chat,payload,source):
    items=normalize(payload)
    with ledger.transaction():
        old=ledger.rows('SELECT result FROM local_batches WHERE source=?',(source,))
        if old:return json.loads(old[0]['result'])
        guard(ledger,chat,payload)
        results=[];seen=set()
        for item in items:
            action,data=item['action'],dict(item['payload'])
            signature=json.dumps(item,sort_keys=True,ensure_ascii=False)
            if signature in seen and not item.get('distinct'):
                raise ValueError('В списке есть одинаковые пункты. Уточните: это один платёж или два разных; одинаковые названия и суммы сами по себе не доказывают дубль.')
            seen.add(signature)
            if action=='schedule':
                # A credit's conditions never generate another payment; a repeated
                # matching monthly obligation is reused instead of added twice.
                existing=[]
                for rule in ledger.rows('SELECT * FROM rules'):
                    if (rule['name'].strip().casefold()==str(data.get('name','')).strip().casefold() and
                        rule['kind']==data.get('kind') and rule['amount']==money(data.get('amount')) and
                        rule['account']==data.get('account') and rule['monthday']==(data.get('monthday') or 0) and
                        (not rule['end'] or rule['end']>=data.get('start','9999-12-31'))):existing.append(rule)
                if existing:
                    selected=next((r for r in existing if r['id']==item.get('existing_id')),None)
                    if selected:
                        results.append({'action':action,'id':selected['id'],'already_exists':True});continue
                    if not item.get('distinct'):raise ValueError('Похожее обязательство уже есть. Уточните, это тот же платёж (existing_id) или другой договор/платёж (distinct).')
            if action=='loan':
                # Compare all known conditions, including dates, not just a bank name.
                target={**data,'principal':money(data['principal']),'payment':money(data['payment']),'fees':money(data.get('fees','0.00'))}
                matches=ledger.rows('SELECT * FROM loans')
                found=next((r for r in matches if json.loads(r['payload'])==target),None)
                if found and item.get('existing_id')==found['id']:results.append({'action':action,'id':found['id'],'already_exists':True});continue
                if found and not item.get('distinct'):raise ValueError('Есть похожий кредит. Нужно уточнить, это тот же договор или другой, прежде чем добавлять.')
            result=getattr(ledger,action)(**data)
            results.append({'action':action,**result,'already_exists':False})
        remaining=payload.get('remaining_intake')
        if not remaining:
            draft=context(ledger,chat).get('draft',{})
            names={str(item['payload'].get('name','')).strip().casefold() for item in items}
            leftover=[item for item in draft.get('items',[]) if item.get('name') and str(item['name']).strip().casefold() not in names and not item.get('do_not_create_approximate_payment')]
            if leftover:remaining={**draft,'items':leftover,'questions':['Какого числа учитывать следующий пункт: '+str(leftover[0]['name'])+'? Если дата пока неизвестна, можно оставить без даты.']}
        if remaining:save(ledger,chat,remaining)
        else:ledger.db.execute('DELETE FROM local_intake WHERE chat=?',(chat,))
        ledger.db.execute('DELETE FROM local_intake_sources WHERE chat=?',(chat,))
        result={'items':results,'fact_expenses_created':0,'remaining_questions':remaining.get('questions',[]) if remaining else []}
        ledger.db.execute('INSERT INTO local_batches VALUES(?,?)',(source,json.dumps(result,ensure_ascii=False)))
        ledger.audit('monthly_batch',{'source':source,'count':len(results)})
        return result
