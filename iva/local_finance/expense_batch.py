"""Atomic recording of an explicit owner's expense list, independent of model prose."""
from datetime import datetime, date, timedelta
import hashlib
import json
import re
from .received_income import raw_owner_input, ZONE, BLOCK
from .paid_expense import NO_EXPENSE, prepare, QUESTIONS
from .assistant import money, rub

PREFIX=re.compile(r'^(?:ива[ ,!:]*)?(?:(?:расходы?\s+(?:внеси|запиши|добавь|учти))|(?:(?:внеси|запиши|добавь|учти)\s+(?:эти\s+)?расходы?))\b\s*(?:эти\b\s*)?[:—-]?\s*',re.I)

def setup(l):
    nested=l.db.in_transaction
    l.db.execute('''CREATE TABLE IF NOT EXISTS local_expense_batches(
      source TEXT PRIMARY KEY,owner INTEGER NOT NULL,raw_hash TEXT NOT NULL,
      fingerprint TEXT NOT NULL,payload TEXT NOT NULL,result TEXT NOT NULL)''')
    if not nested:l.db.commit()

def parse(text,today):
    match=PREFIX.match(text.strip())
    if not match or BLOCK.search(text) or NO_EXPENSE.search(text) or '?' in text:return None
    rest=text.strip()[match.end():]
    when=today
    dm=re.match(r'(сегодня|вчера|позавчера|\d{4}-\d{2}-\d{2})\b\s*[:—-]?\s*',rest,re.I)
    if dm:
        label=dm[1].lower();rest=rest[dm.end():]
        try:when=(date.fromisoformat(today)-timedelta(days={'сегодня':0,'вчера':1,'позавчера':2}[label])).isoformat() if label in ('сегодня','вчера','позавчера') else date.fromisoformat(label).isoformat()
        except ValueError:return {'question':'Уточните дату расходов.'}
        if when>today:return {'question':'Это уже совершённые расходы? Указана будущая дата.'}
    items=[];total=None
    for part in re.split(r'[,](?!\d)|[;\n]+',rest):
        part=part.strip().rstrip('.')
        if not part:continue
        if re.match(r'итого\b|всего\b',part,re.I):
            try:total=money(re.sub(r'^(?:итого|всего)\s*[:=]?\s*|\s*(?:руб\w*|₽)$','',part,flags=re.I))
            except ValueError:return {'question':'Не удалось разобрать итог списка.'}
            continue
        m=re.fullmatch(r'(?:и\s+|ещ[её]\s+|также\s+)?(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?)\s*(?:руб\w*|₽)?\s*(?:это\s+)?(?:на\s+|за\s+)?(.+)',part,re.I)
        if not m or re.search(r'\d|доллар|евро|\busd\b|\beur\b',m[2],re.I):
            return {'question':'Не удалось однозначно разобрать строку «'+part[:120]+'». Укажите сумму в рублях и назначение.'}
        try:amount=money(m[1])
        except ValueError:return {'question':'Уточните сумму в строке «'+part[:120]+'».'}
        if amount<=0:return {'question':'Суммы расходов должны быть положительными.'}
        items.append({'kind':'expense','amount':rub(amount),'date':when,'note':m[2].strip(),'ambiguities':[]})
    if not items or len(items)>30:return {'question':'Укажите от 1 до 30 расходов: сумма и назначение каждой покупки.'}
    if total is not None and total!=sum(money(p['amount']) for p in items):return {'question':'Итог не совпадает с суммой строк. Какую строку нужно исправить?'}
    return {'items':items,'separate':bool(re.search(r'отдельн\w*\s+покуп|друг\w*\s+покуп',text,re.I))}

def record(l,owner,source,text,today=None,*,recover=False):
    setup(l)
    raw=raw_owner_input(l,owner,source,text,require_processing=not recover)
    if not raw:return None
    today=today or datetime.fromtimestamp(raw['message_date'],ZONE).date().isoformat()
    parsed=parse(raw['body'],today)
    if parsed is None:return None
    if parsed.get('question'):return parsed
    with l.transaction():
        previous=l.rows('SELECT * FROM local_expense_batches WHERE source=?',(source,))
        if previous:
            if previous[0]['owner']!=owner or previous[0]['raw_hash']!=raw['hash']:raise PermissionError('Expense list source mismatch')
            return {**json.loads(previous[0]['result']),'duplicate':True}
        items=[]
        for item in parsed['items']:
            item=prepare(l,item,item['note'])
            if item.get('question'):return {'question':QUESTIONS[item['question']]+' Строка: '+item['note']}
            if item['category']=='Не определена' and re.search(r'подар',item['note'],re.I):item['category']='Подарки'
            items.append(item)
        fingerprint=hashlib.sha256(json.dumps(items,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        same=l.rows('SELECT result FROM local_expense_batches WHERE owner=? AND fingerprint=?',(owner,fingerprint))
        if same and not parsed['separate']:return {**json.loads(same[0]['result']),'duplicate':True}
        # Similar old purchases/receipts require a material clarification; do not
        # silently merge them or post the whole list on top of a partial import.
        for item in items:
            similar=l.rows("SELECT id FROM operations WHERE kind='expense' AND amount=? AND on_date=? AND account=? AND void=0",(money(item['amount']),item['date'],item['account']))
            if similar and not parsed['separate']:return {'question':'Расход '+item['amount']+' ₽ ('+item['note']+') похож на уже учтённый. Это та же покупка или отдельная? Список пока не записан.'}
        results=[]
        for index,item in enumerate(items):
            draft=l.stage(item,'iva-expense-list:'+source+':'+str(index))
            op=l.confirm(draft['id'],True,allow_similar=True)
            results.append({**item,'operation_id':op['id']})
        total=rub(sum(money(p['amount']) for p in items))
        answer='Внесла расходы:\n'+'\n'.join(p['amount']+' ₽ — '+p['note'] for p in items)+'\nИтого: '+total+' ₽. Дата: '+items[0]['date']+'.'
        result={'applied':True,'duplicate':False,'items':results,'total':total,'text':answer}
        l.db.execute('INSERT INTO local_expense_batches VALUES(?,?,?,?,?,?)',(source,owner,raw['hash'],fingerprint,json.dumps(items,ensure_ascii=False),json.dumps(result,ensure_ascii=False)))
        l.audit('owner_expense_list_recovered' if recover else 'owner_expense_list_recorded',{'source':source,'operations':[p['operation_id'] for p in results]})
        return result
