"""Standing owner policy: direct documents may refresh an existing matched debt.

No authority comes from text inside a document. This path cannot add a debt,
record cash movement, change payment dates, or mark a payment paid.
"""
import json
import re
from .assistant import money,rub
from .changes import apply_change


def apply_existing_debt_document(ledger,owner,source,action,payload):
    # Serialize validation/read/commit so concurrent re-delivery cannot append
    # notes twice or produce a second posting key.
    with ledger.transaction():
        return _apply(ledger,owner,source,action,payload)


def _apply(ledger,owner,source,action,payload):
    from .paid_expense import envelope
    item=envelope(ledger,owner,source,receipt=True)
    if not item or not item.get('document_id'):
        raise PermissionError('Нужен текущий документ, отправленный владельцем')
    message=json.loads(ledger.rows('SELECT message_json FROM local_telegram_messages WHERE id=?',(item['id'],))[0]['message_json'])
    if any(message.get(k) for k in ('forward_origin','forward_from','forward_sender_name','forward_date','via_bot','quote','external_reply')):
        raise PermissionError('Для пересланного документа нужно отдельное поручение владельца')
    document_source='owner-document:'+item['document_id']
    ledger.db.execute('CREATE TABLE IF NOT EXISTS local_finance_changes(id TEXT PRIMARY KEY,source TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL,result TEXT NOT NULL)')
    prior=ledger.rows('SELECT result FROM local_finance_changes WHERE source=? AND action=?',(document_source,action))
    if prior:return {**json.loads(prior[0]['result']),'duplicate':True}
    documents=ledger.document_context(document_id=item['document_id'])['documents']
    if not documents:raise ValueError('Документ ещё не распознан')
    doc=documents[0]['extraction']
    normalize=lambda s:re.sub(r'[^а-яa-z0-9]','',s.lower().replace('ё','е'))
    bank=normalize(doc.get('bank') or '')
    if len(bank)<3:raise ValueError('Не определён банк документа; уточните, к какому долгу он относится')
    all_debts=ledger.rows('SELECT name FROM reported_loans WHERE active=1')+ledger.rows('SELECT name FROM credit_cards WHERE active=1')
    if sum(bank in normalize(r['name']) for r in all_debts)!=1:
        raise ValueError('У банка несколько обязательств или долг не найден; уточните привязку документа')
    if action=='debt_save':
        candidates=ledger.rows('SELECT * FROM reported_loans WHERE active=1')
        matches=[r for r in candidates if bank in normalize(r['name'])]
        if len(matches)!=1 or matches[0]['debt_ref']!=payload.get('debt_ref'):
            raise ValueError('Нужно однозначно связать документ с существующим долгом')
        old=matches[0]
        balance=payload.get('reported_total_balance')
        preserved={k:old[k] for k in ('debt_ref','name','due_day','planning_from_period','account','issued_month','term_months','product_type')}
        preserved.update(monthly_payment=rub(old['monthly_payment']),payment_is_estimate=bool(old['payment_is_estimate']))
        amount_key='reported_total_balance'
    elif action=='card_save':
        candidates=ledger.rows('SELECT * FROM credit_cards WHERE active=1')
        matches=[r for r in candidates if bank in normalize(r['name'])]
        if len(matches)!=1 or matches[0]['name']!=payload.get('name'):
            raise ValueError('Нужно однозначно связать документ с существующей картой')
        old=matches[0]
        balance=payload.get('balance')
        preserved={k:old[k] for k in ('name','due_day','first_due','account')}
        preserved.update(monthly_payment=rub(old['monthly_payment']),
                         monthly_interest=rub(old['monthly_interest']) if old['monthly_interest'] is not None else None,
                         interest_only=bool(old['interest_only']) if old['interest_only'] is not None else None)
        amount_key='balance'
    else:
        raise ValueError('Документ без подписи разрешает только обновление сведений существующего долга; для иных изменений нужно поручение')
    if balance is None:raise ValueError('В документе нужна точная общая задолженность')
    text='\n'.join(p.get('text','') for p in doc.get('page_texts',[]))
    compact=re.sub(r'\s','',text).replace(',','.')
    amount=rub(money(balance))
    if not re.search(r'(?<![\d.])'+re.escape(amount)+r'(?!\d)',compact):
        raise ValueError('Общая задолженность не подтверждена текстом документа')
    as_of=payload.get('reported_as_of','')
    from datetime import date
    parsed=date.fromisoformat(as_of)
    if as_of not in text and parsed.strftime('%d.%m.%Y') not in text:
        raise ValueError('Дата сведений не подтверждена документом')
    if as_of<old['reported_as_of']:raise ValueError('Документ старее сохранённых сведений')
    notes=payload.get('notes','')
    if not isinstance(notes,str) or not notes.strip():raise ValueError('Нужно сохранить условия из документа')
    # Stable document-scoped source prevents replay through another upload.
    authorized='Обновить сведения уже учтённого кредита по моему документу'
    saved={**preserved,amount_key:amount,'reported_as_of':as_of,
           'notes':old['notes']+'\n\nДокумент '+item['document_id']+' от '+as_of+':\n'+notes}
    return apply_change(ledger,owner,document_source,action,saved,authorized,authorized)
