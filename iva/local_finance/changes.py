"""Owner-authorized ledger edits, atomic and idempotent without approval codes."""
import hashlib
import inspect
import json


def owner_request(ledger,owner,source):
    from .received_income import raw_owner_input
    from .paid_expense import envelope
    return raw_owner_input(ledger,owner,source or '',allow_quoted_text=True) or envelope(ledger,owner,source or '',receipt=True)


def apply_change(ledger,owner,source,action,payload,body,evidence):
    from .assistant import WRITE
    from . import intake
    if action not in WRITE or action=='stage':raise ValueError('Фактические операции записываются через record_actual/record_receipt')
    if not isinstance(payload,dict):raise ValueError('Нужен объект параметров')
    if not body or not isinstance(evidence,str) or len(evidence.strip())<2 or evidence not in body:
        raise PermissionError('Нужна цитата прямого поручения владельца, а не инструкции документа')
    if not isinstance(source,str) or not source:raise PermissionError('Нужен источник поручения')
    data={k:v for k,v in payload.items() if k not in ('source','_allow_similar')}
    encoded=json.dumps(data,ensure_ascii=False,sort_keys=True)
    key=hashlib.sha256((source+'\n'+action+'\n'+encoded).encode()).hexdigest()
    ledger.db.execute('CREATE TABLE IF NOT EXISTS local_finance_changes(id TEXT PRIMARY KEY,source TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL,result TEXT NOT NULL)')
    with ledger.transaction():
        old=ledger.rows('SELECT result FROM local_finance_changes WHERE id=?',(key,))
        if old:return {**json.loads(old[0]['result']),'duplicate':True}
        stable='owner-change:'+source+':'+key[:20]
        if action=='batch':
            result=intake.apply(ledger,owner,data,stable)
        else:
            method=getattr(ledger,action)
            signature=inspect.signature(method)
            if 'source' in signature.parameters or action=='utility_save':data['source']=stable
            if action=='schedule':data.setdefault('lead_days',1)
            signature.bind(**data)
            if action=='loan':
                existing=[r for r in ledger.rows('SELECT id,payload FROM loans') if json.loads(r['payload'])['name'].casefold()==data['name'].casefold()]
                if len(existing)>1:raise ValueError('Есть несколько кредитов с таким названием; уточните кредит')
                result=method(**data)
                if existing:
                    # Preserve the existing loan identity when updating terms.
                    new=ledger.rows('SELECT payload FROM loans WHERE id=?',(result['id'],))[0]
                    ledger.db.execute('UPDATE loans SET payload=? WHERE id=?',(new['payload'],existing[0]['id']))
                    ledger.db.execute('DELETE FROM loans WHERE id=?',(result['id'],))
                    result={**result,'id':existing[0]['id'],'updated':True}
            else:result=method(**data)
            if action=='bank_import':intake.mark_bank(ledger,owner,data['loan_name'],result['id'])
        output={'applied':True,'action':action,'result':result}
        ledger.db.execute('INSERT INTO local_finance_changes VALUES(?,?,?,?,?)',(key,source,action,encoded,json.dumps(output,ensure_ascii=False)))
        ledger.audit('owner_change_applied',{'action':action,'source':source,'id':key})
        return output


def is_live_ledger(ledger):
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    # Never turn isolated tests or the Windows cutover snapshot into live writes.
    if __import__('os').name=='nt':return False
    rows=ledger.db.execute('PRAGMA database_list').fetchall()
    return bool(rows and Path(rows[0][2]).resolve()==(root/'data/local-finance.sqlite3').resolve())


def live_request(ledger,owner,source):
    """Compatibility guard for older attachment/proposal entry points."""
    from pathlib import Path
    if not is_live_ledger(ledger):return None
    root=Path(__file__).resolve().parents[1]
    session=root/'data/iva-telegram/session.json'
    active=json.loads(session.read_text()).get('active_source') if session.exists() else None
    if not active:
        pending=ledger.rows("SELECT id FROM local_updates WHERE chat=? AND state='processing'",(owner,))
        if len(pending)==1:active=f'telegram:{owner}:{pending[0]["id"]}:0'
    raw=owner_request(ledger,owner,active)
    if raw and raw.get('body'):return active,raw['body']
    return None
