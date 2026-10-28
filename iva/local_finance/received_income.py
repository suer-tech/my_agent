"""Narrow server authorization for a direct owner's received-income command.

Neither a model payload, a document nor a historical transcript can mint authority.
The durable Telegram envelope and current processing source are checked again at
commit. Other mutations retain their existing review/confirmation path.
"""
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import hashlib
import json
import re
from .assistant import money,rub

ZONE=timezone(timedelta(hours=5))
BLOCK=re.compile(r'например|пример|допустим|\bесли\b|ожида[юе]|\bжду\b|должн\w*\s+прий|планир|не\s+получ|не\s+(?:внес|добав|запиш|учти|занес)|ещ[её]\s+не|цитат|переслан|в\s+документ|на\s+сайте|он\s+(?:пишет|сказал)|сколько',re.I)
COMMAND=re.compile(r'\b(?:внеси|внести|добавь|добавить|запиши|записать|зафиксируй|учти|занеси)\b',re.I)
RECEIVED=re.compile(r'\b(?:получил[аи]?|полученн\w*|пришл[аои]|зачислил[аи]?|зачислен[аоы]?|поступил[аои]|оплатили|перевели)\b',re.I)


def setup(l):
    nested=l.db.in_transaction
    l.db.execute('''CREATE TABLE IF NOT EXISTS local_received_income_commands(
        source TEXT PRIMARY KEY,origin_update INTEGER NOT NULL,owner INTEGER NOT NULL,
        raw_hash TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
        question TEXT,operation_id TEXT,posting_source TEXT NOT NULL,clarification_update INTEGER)''')
    if not nested:l.db.commit()


def raw_owner_input(l,owner,source,text=None,require_processing=True,allow_quoted_text=False):
    match=re.fullmatch(r'telegram:(\d+):(\d+):0',source)
    if not match or int(match[1])!=owner:return None
    uid=int(match[2]);rows=l.rows('SELECT * FROM local_updates WHERE id=? AND chat=?',(uid,owner))
    raw=l.rows('SELECT message_json FROM local_telegram_messages WHERE id=?',(uid,))
    if not rows or not raw or require_processing and rows[0]['state']!='processing':return None
    msg=json.loads(raw[0]['message_json']);metadata=json.loads(rows[0]['metadata'] or '{}')
    if msg.get('from',{}).get('id')!=owner or msg.get('chat',{}).get('id')!=owner or msg['chat'].get('type')!='private':return None
    if any(msg.get(k) for k in ('forward_origin','forward_from','forward_sender_name','forward_date','quote','external_reply','via_bot','document','photo','audio')):return None
    if any(e.get('type') in ('blockquote','expandable_blockquote','pre','code') for e in msg.get('entities',[])):return None
    if msg.get('voice'):
        body=metadata.get('transcript','')
        if not body or text is not None and text!='Непроверенная транскрипция речи пользователя (суммы и даты подтвердить отдельно):\n'+body:return None
    else:
        body=msg.get('text','')
        if not body or text is not None and text!=body:return None
    if body.lstrip().startswith('>') or not allow_quoted_text and any(q in body for q in ('"','«','»','“','”','\n>')):return None
    return {'id':uid,'body':body,'hash':hashlib.sha256(body.encode()).hexdigest(),'message_date':msg.get('date')}


def parse_command(text,today):
    s=text.strip();lower=s.lower();command=COMMAND.search(lower)
    if re.search(r'\b(?:расход\w*|потратил[аи]?|заплатил[аи]?|оплатил[аи]?|купил[аи]?)\b',lower):return None
    if not command or BLOCK.search(lower):return None
    prefix=lower[:command.start()]
    if not re.fullmatch(r'(?:(?:ива|пожалуйста|давай|можешь|нужно|прошу)[\s,!.:—-]*)*',prefix) and not re.match(r'^(?:ива[, ]+)?(?:я\s+)?(?:уже\s+)?получил',lower):return None
    if not re.search(r'доход|оплат|фриланс|зарплат|выручк|продаж|пособи|пенси',lower):return None
    payload={'kind':'income','date':today,'ambiguities':[],'request_text':s}
    if not RECEIVED.search(lower):return {**payload,'question':'received'}
    # Dates are removed before finding amounts, so day/year digits are not money.
    dated=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',lower)
    if len(dated)>1:return {**payload,'question':'date'}
    if dated:
        try:payload['date']=date.fromisoformat(dated[0]).isoformat()
        except ValueError:return {**payload,'question':'date'}
    elif 'позавчера' in lower:payload['date']=(date.fromisoformat(today)-timedelta(days=2)).isoformat()
    elif 'вчера' in lower:payload['date']=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
    elif re.search(r'\b\d{1,2}[./]\d{1,2}[./]\d{2,4}|\b\d{1,2}\s+(?:январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр)|завтра',lower):return {**payload,'question':'date'}
    if payload['date']>today:return {**payload,'question':'date'}
    value_text=re.sub(r'\b\d{4}-\d{2}-\d{2}\b','',lower)
    words={'один':1,'одна':1,'одну':1,'два':2,'две':2,'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,'восемь':8,'девять':9,'десять':10}
    value_text=re.sub(r'\b('+'|'.join(words)+r')(?=\s+(?:тысяч|рубл))',lambda m:str(words[m[1]]),value_text)
    numbers=list(re.finditer(r'(?<!\w)(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?)\s*(тыс\w*|тысяч\w*|к\b|руб\w*|₽)?',value_text))
    if len(numbers)!=1:return {**payload,'question':'amount'}
    n=numbers[0];value=Decimal(n[1].replace(' ','').replace('\u00a0','').replace(',','.'))
    if n[2] and (n[2].startswith('тыс') or n[2]=='к'):value*=1000
    try:
        payload['amount']=rub(money(format(value,'f')))
        if money(payload['amount'])<=0:raise ValueError()
    except (ValueError,ArithmeticError):return {**payload,'question':'amount'}
    purpose=re.search(r'\b(?:за|по|от)\s+(.+)',s,re.I)
    note=re.split(r'\b(?:я\s+)?(?:уже\s+)?получил|\bсегодня\b|\bвчера\b|\d',purpose[1],maxsplit=1,flags=re.I)[0].strip(' ,.;-') if purpose else ''
    if not note:
        note=next((name for term,name in [('фриланс','Фриланс'),('зарплат','Зарплата'),('пенси','Пенсия'),('пособи','Пособие'),('продаж','Продажа'),('выручк','Выручка')] if term in lower),'')
    if not note:return {**payload,'question':'purpose'}
    payload['note']=note[:250];payload['separate_receipt']=bool(re.search(r'\bещ[её]\b|\bвтор\w*|\bдруг\w*\s+поступ|отдельн\w*\s+(?:доход|оплат|поступ)|нов\w*\s+поступ',lower))
    return payload


QUESTIONS={'received':'Этот доход уже получен или пока ожидается?','amount':'Какую сумму полученного дохода записать?','date':'Какой датой получен этот доход?','purpose':'Откуда поступил этот доход?','account':'На какой из сохранённых денежных счетов поступил доход?','duplicate':'Это ещё одно отдельное поступление или тот же доход, который уже записан?'}


def authorize_current(l,owner,source,text,today=None):
    setup(l);raw=raw_owner_input(l,owner,source,text)
    if not raw:return None
    old=l.rows('SELECT * FROM local_received_income_commands WHERE source=?',(source,))
    if old:return old[0]
    today=today or (datetime.fromtimestamp(raw['message_date'],ZONE).date().isoformat() if raw.get('message_date') else datetime.now(ZONE).date().isoformat())
    p=parse_command(raw['body'],today)
    previous=None
    if p is None:
        waiting=l.rows("SELECT * FROM local_received_income_commands WHERE owner=? AND state='needs_clarification' ORDER BY origin_update DESC LIMIT 1",(owner,))
        if not waiting:return None
        previous=waiting[0];saved=json.loads(previous['payload']);q=previous['question'];answer=raw['body'].strip().lower()
        if BLOCK.search(answer) or any(v in answer for v in ('?','«','»','"')):return None
        if q=='received' and re.fullmatch(r'(?:я\s+)?(?:уже\s+)?получил(?:а)?(?:\s+уже)?[.! ]*',answer):p=parse_command(saved['request_text']+' '+raw['body'],saved['date'])
        elif q=='amount' and re.fullmatch(r'[\d\s.,]+(?:тыс\w*|тысяч\w*|руб\w*|₽|к)?[ .]*',answer):
            p=parse_command(saved['request_text']+' '+raw['body'],saved['date'])
        elif q=='purpose' and re.match(r'^(?:за|по|от)\s+',answer):p=parse_command(saved['request_text']+' '+raw['body'],saved['date'])
        elif q=='duplicate' and re.search(r'\b(?:ещ[её]|отдельн\w*|втор\w*|друг\w*)',answer):p={**saved,'separate_receipt':True}
        elif q=='duplicate' and re.search(r'тот\s+же|повтор|уже\s+(?:внес|запис|учт)',answer):
            op=l.rows("SELECT id FROM operations WHERE kind='income' AND amount=? AND on_date=? AND account=? AND void=0",(money(saved['amount']),saved['date'],saved['account']))
            if not op:return None
            p={**saved,'clarification_hash':raw['hash']}
            with l.transaction():l.db.execute("UPDATE local_received_income_commands SET state='done',operation_id=?,clarification_update=?,payload=? WHERE source=?",(op[0]['id'],raw['id'],json.dumps(p,ensure_ascii=False),previous['source']))
            return current_command(l,source)
        elif q=='date' and (answer in ('сегодня','вчера','позавчера') or re.fullmatch(r'\d{4}-\d{2}-\d{2}',answer)):
            clean=re.sub(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?|\b\d{1,2}\s+(?:январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*|завтра','',saved['request_text'],flags=re.I)
            p=parse_command(clean+' '+answer,today)
        elif q=='account':
            matches=l.rows('SELECT id,name FROM accounts');matches=[a for a in matches if answer in (a['id'].lower(),a['name'].lower())]
            if len(matches)==1:p={**saved,'account':matches[0]['id']}
        if not p:return None
    accounts=l.rows('SELECT id FROM accounts')
    if len(accounts)==1:p['account']=accounts[0]['id']
    elif p.get('account'):pass
    elif not p.get('question'):p['question']='account'
    if not p.get('question'):
        try:l.validate_operation({k:v for k,v in p.items() if k in ('kind','amount','date','account','note','ambiguities')})
        except ValueError:p['question']='date'
    if not p.get('question') and not p['separate_receipt']:
        similar=l.rows("SELECT * FROM operations WHERE kind='income' AND amount=? AND on_date=? AND account=? AND void=0",(money(p['amount']),p['date'],p['account']))
        if similar:p['question']='duplicate'
    question=p.pop('question',None)
    with l.transaction():
        if previous:
            p['clarification_hash']=raw['hash']
            l.db.execute('UPDATE local_received_income_commands SET payload=?,state=?,question=?,clarification_update=? WHERE source=?',(json.dumps(p,ensure_ascii=False),'needs_clarification' if question else 'authorized',question,raw['id'],previous['source']))
        else:l.db.execute('INSERT INTO local_received_income_commands VALUES(?,?,?,?,?,?,?,?,?,?)',(source,raw['id'],owner,raw['hash'],json.dumps(p,ensure_ascii=False),'needs_clarification' if question else 'authorized',question,None,'iva-received-income:'+source,None))
    return current_command(l,source)


def current_command(l,source):
    rows=l.rows('SELECT * FROM local_received_income_commands WHERE source=?',(source,))
    if not rows:
        match=re.fullmatch(r'telegram:\d+:(\d+):0',source)
        if match:rows=l.rows('SELECT * FROM local_received_income_commands WHERE clarification_update=? ORDER BY origin_update DESC LIMIT 1',(int(match[1]),))
    return rows[0] if rows else None


def public_status(l,source):
    setup(l);row=current_command(l,source)
    if not row:return None
    p=json.loads(row['payload'])
    return {'source':source,'state':row['state'],'amount':p.get('amount'),'date':p.get('date'),'purpose':p.get('note'),'question':QUESTIONS.get(row['question'])}


def apply_current(l,owner,source,*,trusted_recovery=False):
    setup(l)
    with l.transaction():
        row=current_command(l,source)
        if not row or row['owner']!=owner:raise PermissionError('Нет авторизации текущего полученного дохода')
        if not trusted_recovery:
            raw=raw_owner_input(l,owner,source)
            p=json.loads(row['payload'])
            valid=raw and (raw['id']==row['origin_update'] and raw['hash']==row['raw_hash'] or raw['id']==row['clarification_update'] and raw['hash']==p.get('clarification_hash'))
            if not valid:raise PermissionError('Доход не связан с текущим сообщением владельца')
        if row['state']=='done':return receipt(l,row,duplicate=True)
        if row['state']!='authorized':raise ValueError(QUESTIONS.get(row['question'],'Нужны данные дохода'))
        p=json.loads(row['payload'])
        if p.get('kind')!='income' or p.get('ambiguities'):raise PermissionError('Разрешён только полученный доход')
        data={k:p[k] for k in ('kind','amount','date','account','note','ambiguities')}
        existing=l.rows('SELECT id FROM operations WHERE source=?',(row['posting_source'],))
        if existing:operation=existing[0]['id']
        else:
            draft=l.stage(data,row['posting_source']);result=l.confirm(draft['id'],True,allow_similar=bool(p.get('separate_receipt')))
            if result.get('possible_duplicate'):raise ValueError(QUESTIONS['duplicate'])
            operation=result['id']
        l.db.execute("UPDATE local_received_income_commands SET state='done',operation_id=? WHERE source=?",(operation,row['source']))
        l.audit('explicit_owner_received_income',{'source':source,'origin_update':row['origin_update'],'operation':operation,'no_second_confirmation':True})
        row={**row,'operation_id':operation,'state':'done'}
        return receipt(l,row)


def receipt(l,row,duplicate=False):
    p=json.loads(row['payload'])
    return {'applied':True,'duplicate':duplicate,'operation_id':row['operation_id'],'amount':p['amount'],'date':p['date'],'purpose':p['note'],'source':row['source'],'text':f"{'Этот доход уже внесён' if duplicate else 'Внесла доход'}: {p['amount']} ₽, {p['date']}, источник — {p['note']}."}


def recover_existing_proposal(l,owner,proposal_id,origin_source,clarification_source):
    """Operator-only migration of the specific formerly reviewed income.

    Not a gateway action, Ledger method, or model tool. The current human policy
    change authorizes this recovery; the old envelopes and exact proposal still
    have to establish the same received income.
    """
    setup(l)
    original=raw_owner_input(l,owner,origin_source,require_processing=False)
    clarification=raw_owner_input(l,owner,clarification_source,require_processing=False)
    rows=l.rows("SELECT * FROM local_pending WHERE id=? AND chat=? AND action='stage'",(proposal_id,owner))
    if not original or not clarification or not rows:raise PermissionError('Не найдены исходная команда, уточнение и предложение')
    if not re.fullmatch(r'получил(?:а)?\s+уже[.! ]*',clarification['body'],re.I):raise ValueError('Факт получения не уточнён')
    pending=rows[0];p=json.loads(pending['payload'])['data']
    parsed=parse_command(original['body']+' '+clarification['body'],p['date'])
    if not parsed or parsed.get('question') or parsed['kind']!='income' or money(parsed['amount'])!=money(p['amount']):raise PermissionError('Исходный доход не совпадает с предложением')
    if p.get('kind')!='income' or p.get('ambiguities') or p.get('occurrence') or p.get('target'):raise PermissionError('Предложение не является однозначным разовым доходом')
    accounts=l.rows('SELECT id FROM accounts')
    if len(accounts)!=1 or accounts[0]['id']!=p['account']:raise ValueError('Нужно уточнить денежный счёт')
    payload={**p,'request_text':original['body'],'separate_receipt':False}
    with l.transaction():
        l.db.execute('INSERT OR IGNORE INTO local_received_income_commands VALUES(?,?,?,?,?,?,?,?,?,?)',(origin_source,original['id'],owner,original['hash'],json.dumps(payload,ensure_ascii=False),'authorized',None,None,pending['source'],None))
        result=apply_current(l,owner,origin_source,trusted_recovery=True)
        l.db.execute("UPDATE local_pending SET state='done' WHERE id=?",(proposal_id,))
        l.audit('received_income_policy_recovery',{'proposal':proposal_id,'origin':original['id'],'receipt_clarification':clarification['id'],'operation':result['operation_id']})
    return result
