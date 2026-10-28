"""Store Iva's semantic reply-format decisions for the authenticated owner turn."""
import json


def execute(ledger,owner,source,data):
    rows=ledger.rows("SELECT value FROM local_meta WHERE key='voice_mode'")
    current=rows[0]['value'] if rows else 'off'
    if data.get('action')=='status':return {'ok':True,'mode':current}
    if data.get('action')!='set' or data.get('format') not in ('voice','text') or data.get('scope') not in ('once','persistent'):
        raise ValueError('Invalid reply format or scope')
    from .received_income import raw_owner_input
    raw=raw_owner_input(ledger,owner,source or '')
    if not raw:raise PermissionError('Reply mode requires the current direct owner message')
    uid=raw['id'];metadata=json.loads(ledger.rows('SELECT metadata FROM local_updates WHERE id=?',(uid,))[0]['metadata'] or '{}')
    voice=data['format']=='voice'
    result={'ok':True,'format':data['format'],'scope':data['scope'],'source':source}
    metadata['reply_voice_requested']=voice
    metadata['reply_format_result']=result
    with ledger.db:
        if data['scope']=='persistent':ledger.db.execute("INSERT OR REPLACE INTO local_meta VALUES('voice_mode',?)",('on' if voice else 'off',))
        ledger.db.execute('UPDATE local_updates SET metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),uid))
    return result
