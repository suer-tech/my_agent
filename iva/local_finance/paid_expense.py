"""Authenticated paid-expense commands and receipt facts; no generic model commit."""
from datetime import datetime
import hashlib
import json
import re
from .assistant import money,rub
from .received_income import raw_owner_input,parse_command,ZONE,BLOCK

SPENT=re.compile(r'\b(?:потратил[аи]?|заплатил[аи]?|оплатил[аи]?|купил[аи]?)\b',re.I)
NO_EXPENSE=re.compile(r'пример|например|допустим|\bесли\b|планир|собираюсь|\bжду\b|ожида|не\s+(?:потратил|оплатил|заплатил|купил)|возврат|перевод\s+между|снял\s+налич|в\s+документ|цитат',re.I)
QUESTIONS={'amount':'Какую сумму фактически потратили?','date':'Какая точная дата покупки?','currency':'Какую сумму в рублях нужно учесть?','account':'С какого из сохранённых денежных счетов оплачен расход?','budget':'К какой статье бюджета отнести эту покупку?','obligation':'Какой именно платёж по обязательству вы оплатили?','duplicate':'Это та же уже записанная покупка или ещё одна отдельная покупка?','paid':'Этот документ подтверждает уже совершённую оплату или это счёт к оплате?','confidence':'Какие итоговую сумму и дату покупки считать верными?','mismatch':'Сумма в сообщении отличается от итога чека. Какую сумму вы фактически оплатили?','items':'Сумма позиций не сходится с итогом. Какой итог с учётом скидок верный?'}
CATEGORIES=[('original-monthly-line-16','Детское питание',r'детск\w*\s+(?:питан|смес)|смесь\s+(?:nan|nutrilon)'),('original-monthly-line-17','Бензин',r'бензин|топлив|заправ|\bазс\b|аи[ -]?(?:92|95|98)'),('original-monthly-line-18','Автобус',r'автобус|проезд|билет\s+на\s+автобус'),('original-monthly-line-15','Продукты',r'продукт|\bхлеб\b|\bмолоко\b|\bсыр\b|\bяйца\b')]


def setup(l):
    nested=l.db.in_transaction
    l.db.execute('''CREATE TABLE IF NOT EXISTS local_paid_expense_commands(source TEXT PRIMARY KEY,owner INTEGER NOT NULL,origin_update INTEGER NOT NULL,raw_hash TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,question TEXT,operation_id TEXT,document_id TEXT,clarification_source TEXT)''')
    l.db.execute('''CREATE TABLE IF NOT EXISTS local_paid_receipt_links(document_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES operations(id),fiscal_fingerprint TEXT,semantic_fingerprint TEXT NOT NULL)''')
    if not nested:l.db.commit()


def current(l,source):
    rows=l.rows('SELECT * FROM local_paid_expense_commands WHERE source=? OR clarification_source=? ORDER BY origin_update DESC LIMIT 1',(source,source))
    return rows[0] if rows else None


def envelope(l,owner,source,text=None,receipt=False,processing=True):
    if not receipt:return raw_owner_input(l,owner,source,text,require_processing=processing)
    match=re.fullmatch(r'telegram:(\d+):(\d+):0',source)
    if not match or int(match[1])!=owner:return None
    rows=l.rows('SELECT * FROM local_updates WHERE id=? AND chat=?',(int(match[2]),owner));raw=l.rows('SELECT message_json FROM local_telegram_messages WHERE id=?',(int(match[2]),))
    if not rows or not raw or processing and rows[0]['state']!='processing':return None
    msg=json.loads(raw[0]['message_json']);meta=json.loads(rows[0]['metadata'] or '{}')
    if msg.get('from',{}).get('id')!=owner or msg.get('chat',{}).get('id')!=owner or msg['chat'].get('type')!='private':return None
    if not (msg.get('photo') or msg.get('document')):return None
    if NO_EXPENSE.search(msg.get('caption','')) or re.search(r'не\s+мой|чужой\s+чек',msg.get('caption',''),re.I):return None
    return {'id':int(match[2]),'body':msg.get('caption',''),'hash':hashlib.sha256(raw[0]['message_json'].encode()).hexdigest(),'document_id':meta.get('document_id'),'message_date':msg.get('date')}


def parse_text(text,today):
    s=text.strip()
    # A bank debit is a paid expense even when the owner mentions the separate
    # funding/top-up amount in the same message. Never turn that top-up into income.
    debits=list(re.finditer(r'\b(?:списал[аи]?|списалось|списано|списаны)\s+(?:сумму\s+|в\s+размере\s+)?(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?)\s*(?:руб\w*|₽)?',s,re.I))
    if debits and not NO_EXPENSE.search(s) and not re.search(r'\?|возможно|кажется|якобы|\bне\s+спис|\b(?:он|она|ему|ей|него|неё|нее|клиент|друг)\b|(?:говорит|сказал|написал)',s,re.I):
        if len(debits)!=1:return {'kind':'expense','request_text':s,'date':today,'question':'amount'}
        p={'kind':'expense','amount':rub(money(debits[0][1])),'date':today,'note':s[:500],'request_text':s,'bank_debit':True}
        # Resolve date via the same trusted date rules as ordinary expenses,
        # using a synthetic single-amount sentence ONLY for parsing the date.
        date_hint=re.search(r'\b\d{4}-\d{2}-\d{2}\b|позавчера|вчера|сегодня|завтра',s,re.I)
        dated=parse_command('Запиши доход получил '+p['amount']+' за операцию '+(date_hint[0] if date_hint else ''),today)
        if dated and dated.get('question'):p['question']=dated['question']
        elif dated:p['date']=dated['date']
        funding=re.search(r'(?<!\w)(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?)\s*(?:руб\w*|₽)?\s+(?:внес|добавил|дополнил)',s,re.I)
        if funding:p['unresolved_funding']=rub(money(funding[1]))
        return p
    v=SPENT.search(s)
    if not v or NO_EXPENSE.search(s):return None
    prefix=s[:v.start()].lower()
    if not re.fullmatch(r'(?:(?:ива|я|сегодня|вчера|позавчера|запиши|внеси|добавь|пожалуйста|расход|расходы)[\s,!.:—-]*)*',prefix):return None
    if re.search(r'доллар|евро|\b(?:usd|eur|kzt|cny)\b',s,re.I):return {'kind':'expense','request_text':s,'date':today,'question':'currency'}
    converted=SPENT.sub('получил',s)
    converted=re.sub(r'^(?:(?:ива|я|сегодня|вчера|позавчера|запиши|внеси|добавь|пожалуйста|расход|расходы)[\s,!.:—-]*)+','',converted,flags=re.I)
    converted=re.sub(r'\bна\s+','за ',converted,flags=re.I)
    p=parse_command('Запиши доход '+converted+(' вчера' if 'вчера' in s.lower() and 'вчера' not in converted.lower() else ''),today)
    if not p:return None
    p['kind']='expense';p['request_text']=s
    if p.get('question')=='purpose':p.pop('question');p['note']='Некатегоризированный расход'
    p.setdefault('note',s[:250]);p.setdefault('separate_receipt',bool(re.search(r'\bещ[её]\b|\bдруг\w*\s+покуп|\bотдельн\w*\s+покуп',s,re.I)))
    return p


def classify(l,p,text):
    matches=[(ref,label) for ref,label,pattern in CATEGORIES if re.search(pattern,text,re.I) and l.rows('SELECT ref FROM family_expenses WHERE ref=?',(ref,))]
    if len(matches)>1:return 'budget'
    if matches:p['budget_ref']=matches[0][0];p['category']=matches[0][1]
    else:p['category']='Не определена'
    debt=bool(re.search(r'процент|погас|(?:по|за)\s+(?:кредит|займ)|оплатил[аи]?\s+(?:кредит|займ)|плат[её]ж.{0,25}(?:кредит|халва|сбер|совком|альфа|т[ -]?банк)',text,re.I))
    if debt:
        aliases=[a for a in ('альфа','совком','сбер','тбанк','тинькофф','халва') if a in re.sub(r'[^а-яa-z]','',text.lower())]
        first=p['date'][:7]+'-01'
        from finance.core import month_day
        last=month_day(int(first[:4]),int(first[5:7]),31).isoformat()
        events=[e for e in l.events(first,last) if e['kind']=='expense' and e.get('amount')==money(p['amount']) and any(a in re.sub(r'[^а-яa-z]','',e['name'].lower()) for a in aliases)]
        if len(events)!=1:return 'obligation'
        p.pop('budget_ref',None);p['occurrence']=events[0]['occurrence'];p['category']='Платёж по обязательству'
    return None


def prepare(l,p,text):
    if p.get('question'):return p
    accounts=l.rows('SELECT id FROM accounts')
    if not p.get('account'):
        if len(accounts)!=1:return {**p,'question':'account'}
        p['account']=accounts[0]['id']
    question=classify(l,p,text)
    if question:return {**p,'question':question}
    try:l.validate_operation({**p,'ambiguities':[]})
    except ValueError:return {**p,'question':'date'}
    return p


def save(l,owner,source,raw,p,document_id=None):
    question=p.pop('question',None)
    if not question and not p.get('separate_receipt') and not p.get('reconcile_operation'):
        same=l.rows("SELECT id FROM operations WHERE kind='expense' AND amount=? AND on_date=? AND account=? AND void=0",(money(p['amount']),p['date'],p['account']))
        if same:question='duplicate'
    with l.transaction():l.db.execute('INSERT INTO local_paid_expense_commands VALUES(?,?,?,?,?,?,?,?,?,NULL)',(source,owner,raw['id'],raw['hash'],json.dumps(p,ensure_ascii=False),'needs_clarification' if question else 'authorized',question,None,document_id))
    return current(l,source)


def authorize_text(l,owner,source,text,today=None,*,recover=False):
    setup(l);raw=envelope(l,owner,source,text,processing=not recover)
    if not raw:return None
    previous=current(l,source)
    if previous:return previous
    today=today or (datetime.fromtimestamp(raw['message_date'],ZONE).date().isoformat() if raw.get('message_date') else datetime.now(ZONE).date().isoformat())
    p=parse_text(raw['body'],today)
    if p:return save(l,owner,source,raw,prepare(l,p,raw['body']))
    waiting=l.rows("SELECT * FROM local_paid_expense_commands WHERE owner=? AND state='needs_clarification' ORDER BY origin_update DESC LIMIT 1",(owner,))
    if not waiting or NO_EXPENSE.search(raw['body']):return None
    row=waiting[0];p=json.loads(row['payload']);answer=raw['body'].strip();q=row['question'];new=None
    if q=='duplicate':
        if re.search(r'\bещ[её]\b|другая|отдельная|новая',answer,re.I):new={**p,'separate_receipt':True};new.pop('reconcile_operation',None)
        elif re.search(r'та\s+же|тот\s+же|уже\s+(?:внес|учт|запис)|предыдущ',answer,re.I):
            same=l.rows("SELECT id FROM operations WHERE kind='expense' AND amount=? AND on_date=? AND account=? AND void=0",(money(p['amount']),p['date'],p['account']))
            if len(same)==1:new={**p,'reconcile_operation':same[0]['id']}
    elif q=='amount' and re.fullmatch(r'[\d\s.,]+(?:руб\w*|₽|тыс\w*)?[ .]*',answer):new=parse_text(p['request_text']+' '+answer,p['date'])
    elif q=='budget':
        candidate={**p};question=classify(l,candidate,answer)
        if not question and candidate.get('budget_ref'):new=candidate
        elif re.fullmatch(r'прочее|другое|без категории|не определена',answer,re.I):new={**p,'category':'Не определена'};new.pop('budget_ref',None)
    elif q=='account':
        accounts=l.rows('SELECT id,name FROM accounts');matches=[a for a in accounts if answer.lower() in (a['id'].lower(),a['name'].lower())]
        if len(matches)==1:new={**p,'account':matches[0]['id']}
    elif q=='obligation':
        candidate={**p}
        if not classify(l,candidate,answer) and candidate.get('occurrence'):new=candidate
    elif q in ('mismatch','items') and re.fullmatch(r'[\d\s.,]+(?:руб\w*|₽)?[ .]*',answer):
        value=re.sub(r'\s*(?:руб\w*|₽)\s*$','',answer,flags=re.I).strip()
        try:
            if money(value)>0:new={**p,'amount':rub(money(value)),'owner_amount_override':True}
        except ValueError:pass
    elif q=='date' and (answer.lower() in ('сегодня','вчера') or re.fullmatch(r'\d{4}-\d{2}-\d{2}',answer)):
        from datetime import date,timedelta
        when=today if answer.lower()=='сегодня' else (date.fromisoformat(today)-timedelta(days=1)).isoformat() if answer.lower()=='вчера' else answer
        try:
            if date.fromisoformat(when).isoformat()<=today:new={**p,'date':when,'owner_date_override':True}
        except ValueError:pass
    elif q=='confidence' and (re.search(r'\d{4}-\d{2}-\d{2}',answer) or re.search(r'сегодня|вчера',answer,re.I)):
        resolved=parse_text('Потратил '+answer,today)
        if resolved and not resolved.get('question') and resolved.get('amount'):
            new={**p,'amount':resolved['amount'],'date':resolved['date'],'owner_amount_and_date_resolution':True}
    elif q=='paid' and re.fullmatch(r'(?:да[, ]*)?(?:я\s+)?(?:уже\s+)?оплатил(?:а)?(?:\s+сегодня)?[.! ]*|оплачено[.! ]*',answer,re.I):
        doc=l.rows('SELECT extraction FROM source_documents WHERE id=?',(row['document_id'],))
        r=json.loads(doc[0]['extraction']).get('receipt',{}) if doc else {}
        if r.get('confidence')=='high' and p.get('amount') and p.get('date'):
            new={**p,'owner_paid_clarification':True}
            if 'сегодня' in answer.lower():new['date']=today
            else:new['question']='date'
    if not new:return None
    new=prepare(l,new,answer if q in ('budget','obligation') else p.get('request_text',''))
    question=new.pop('question',None);new['clarification_hash']=raw['hash']
    with l.transaction():l.db.execute('UPDATE local_paid_expense_commands SET payload=?,state=?,question=?,clarification_source=? WHERE source=?',(json.dumps(new,ensure_ascii=False),'needs_clarification' if question else 'authorized',question,source,row['source']))
    return current(l,source)


def authorize_receipt(l,owner,source,sha,caption):
    setup(l);raw=envelope(l,owner,source,receipt=True)
    if not raw or raw['document_id']!=sha:return None
    existing=current(l,source)
    if existing:return existing
    docs=l.rows("SELECT * FROM source_documents WHERE id=? AND kind='receipt'",(sha,))
    if not docs:return None
    data=json.loads(docs[0]['extraction']);r=data.get('receipt') or {}
    p={'kind':'expense','amount':r.get('total'),'date':r.get('date'),'note':str(r.get('merchant') or 'Покупка по чеку')[:250],'ambiguities':[],'request_text':caption,'source_document':sha,'separate_receipt':bool(re.search(r'ещ[её]\s+одна|другая\s+покупка|отдельная\s+покупка',caption,re.I))}
    from .attachments import validate_receipt
    questions=validate_receipt(data)
    if r.get('payment_status')!='paid':p['question']='paid'
    elif r.get('confidence')!='high' or data.get('questions'):p['question']='confidence'
    elif not r.get('paid_evidence') or not any(re.search(r'кассов|фискальн|приход|оплачен|оплата|расч[её]т',s,re.I) for s in r['paid_evidence']):p['question']='paid'
    elif questions:p['question']='items' if any('позиц' in q for q in questions) else 'confidence'
    evidence=' '.join(r.get('paid_evidence',[]))
    if re.search(r'сч[её]т\s+на\s+оплату|не\s+оплачен|график\s+платеж|возврат|перевод',evidence,re.I):p['question']='paid'
    # A caption cannot silently override a different printed total.
    numbers=re.findall(r'(?<!\w)(\d+(?:[.,]\d{1,2})?)\s*(?:руб\w*|₽)',caption,re.I)
    if r.get('total') and numbers and any(money(n)!=money(r['total']) for n in numbers):p['question']='mismatch'
    today=datetime.fromtimestamp(raw['message_date'],ZONE).date().isoformat() if raw.get('message_date') else datetime.now(ZONE).date().isoformat()
    said=parse_text(caption,today)
    if said and said.get('amount') and r.get('total') and money(said['amount'])!=money(r['total']):p['question']='mismatch'
    if r.get('date') and r['date']>today:p['question']='date'
    if not p.get('question'):
        text=caption+' '+' '.join(str(i.get('name','')) for i in r.get('items',[]))
        p=prepare(l,p,text)
    fiscal=[r.get(k) for k in ('fiscal_number','fiscal_document','fiscal_sign')]
    p['fiscal_fingerprint']=hashlib.sha256(('|'.join(fiscal)).encode()).hexdigest() if all(isinstance(v,str) and v.isdigit() for v in fiscal) and len(fiscal[0])>=10 else None
    p['semantic_fingerprint']=hashlib.sha256(json.dumps([r.get('merchant'),r.get('date'),r.get('total'),[(i.get('name'),i.get('line_total')) for i in r.get('items',[])]],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    linked=l.rows('SELECT * FROM local_paid_receipt_links WHERE document_id=?',(sha,))
    if not linked and p['fiscal_fingerprint']:linked=l.rows('SELECT * FROM local_paid_receipt_links WHERE fiscal_fingerprint=?',(p['fiscal_fingerprint'],))
    if linked:
        p['reconcile_operation']=linked[0]['operation_id']
        op=l.rows('SELECT * FROM operations WHERE id=? AND void=0',(linked[0]['operation_id'],))
        if not op or not p.get('amount') or op[0]['amount']!=money(p['amount']) or op[0]['on_date']!=p['date']:p['question']='mismatch'
    if not p.get('question') and not linked:
        candidates=l.rows("SELECT DISTINCT o.id FROM operations o JOIN local_paid_expense_commands c ON c.operation_id=o.id WHERE o.kind='expense' AND o.amount=? AND o.on_date=? AND o.account=? AND o.void=0 AND c.document_id IS NULL",(money(p['amount']),p['date'],p['account']))
        if len(candidates)==1 and re.search(r'тот\s+же|та\s+же|предыдущ|ранее\s+записан',caption,re.I):p['reconcile_operation']=candidates[0]['id']
        if p.get('fiscal_fingerprint') and not candidates:
            same=l.rows("SELECT o.id,r.fiscal_fingerprint FROM operations o LEFT JOIN local_paid_receipt_links r ON r.operation_id=o.id WHERE o.kind='expense' AND o.amount=? AND o.on_date=? AND o.account=? AND o.void=0",(money(p['amount']),p['date'],p['account']))
            if same and all(s['fiscal_fingerprint'] and s['fiscal_fingerprint']!=p['fiscal_fingerprint'] for s in same):
                p['separate_receipt']=True;p['separate_receipt_evidence']='Distinct fiscal receipt identity'
    return save(l,owner,source,raw,p,sha)


def apply_current(l,owner,source,*,recover=False):
    setup(l)
    with l.transaction():
        row=current(l,source)
        if not row or row['owner']!=owner:raise PermissionError('Нет авторизации текущего расхода')
        p=json.loads(row['payload'])
        if row['clarification_source']==source:raw=envelope(l,owner,source,processing=not recover);valid=raw and raw['hash']==p.get('clarification_hash')
        else:
            raw=envelope(l,owner,source,receipt=bool(row['document_id']),processing=not recover);valid=raw and raw['id']==row['origin_update'] and raw['hash']==row['raw_hash']
        if not valid:raise PermissionError('Расход не связан с текущим сообщением владельца')
        if row['state']=='done':return result(row,p,True)
        if row['state']!='authorized':raise ValueError(QUESTIONS[row['question']])
        if p.get('kind')!='expense':raise PermissionError('Разрешён только совершённый расход')
        if row['document_id']:
            links=l.rows('SELECT * FROM local_paid_receipt_links WHERE document_id=?',(row['document_id'],))
            if not links and p.get('fiscal_fingerprint'):links=l.rows('SELECT * FROM local_paid_receipt_links WHERE fiscal_fingerprint=?',(p['fiscal_fingerprint'],))
            if links:p['reconcile_operation']=links[0]['operation_id']
        if p.get('reconcile_operation'):
            op=l.rows("SELECT * FROM operations WHERE id=? AND kind='expense' AND void=0",(p['reconcile_operation'],))
            if not op or op[0]['amount']!=money(p['amount']) or op[0]['on_date']!=p['date'] or op[0]['account']!=p['account']:raise ValueError(QUESTIONS['duplicate'])
            operation=op[0]['id']
            existing=l.rows('SELECT ref FROM family_expense_receipts WHERE operation_id=?',(operation,))
            if existing and p.get('budget_ref') and existing[0]['ref']!=p['budget_ref']:raise ValueError(QUESTIONS['budget'])
            if p.get('budget_ref') and not existing:
                l.db.execute('INSERT INTO family_expense_receipts VALUES(?,?,?)',(operation,p['budget_ref'],p['date'][:7]))
        else:
            fields=('kind','amount','date','account','note','budget_ref','occurrence','source_document')
            data={k:p[k] for k in fields if p.get(k) is not None};data['ambiguities']=[]
            posting='document:'+row['document_id'] if row['document_id'] else 'iva-paid-expense:'+row['source']
            draft=l.stage(data,posting);committed=l.confirm(draft['id'],True,allow_similar=bool(p.get('separate_receipt')))
            if committed.get('possible_duplicate'):raise ValueError(QUESTIONS['duplicate'])
            operation=committed['id']
        if row['document_id']:
            l.db.execute('INSERT OR IGNORE INTO local_paid_receipt_links VALUES(?,?,?,?)',(row['document_id'],operation,p.get('fiscal_fingerprint'),p['semantic_fingerprint']))
        l.db.execute("UPDATE local_paid_expense_commands SET state='done',operation_id=? WHERE source=?",(operation,row['source']))
        l.audit('explicit_owner_paid_expense',{'source':row['source'],'operation':operation,'document':row['document_id'],'reconciled':bool(p.get('reconcile_operation'))})
        return result({**row,'operation_id':operation},p,bool(p.get('reconcile_operation')))


def result(row,p,duplicate=False):
    text=f"{'Этот расход уже учтён' if duplicate else 'Внесла расход'}: {p['amount']} ₽, {p['date']}. Категория: {p.get('category','Не определена')}."
    if p.get('occurrence'):text+=' Платёж связан с графиком и больше не входит в неоплаченные. Сам кредит не закрыт.'
    if p.get('unresolved_funding'):text+=' Пополнение '+p['unresolved_funding']+' ₽ не записано как доход: это перевод из уже учтённых денег или отдельное поступление извне?'
    return {'applied':True,'duplicate':duplicate,'operation_id':row['operation_id'],'amount':p['amount'],'date':p['date'],'category':p.get('category','Не определена'),'occurrence':p.get('occurrence'),'unresolved_funding':p.get('unresolved_funding'),'text':text}


def public_status(l,source):
    setup(l);row=current(l,source)
    if not row:return None
    p=json.loads(row['payload'])
    return {'source':source,'state':row['state'],'amount':p.get('amount'),'date':p.get('date'),'question':QUESTIONS.get(row['question'])}
