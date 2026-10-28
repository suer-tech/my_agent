"""Outbound-only Telegram transport; no competing getUpdates loop."""
import json
import os
import urllib.request
import uuid


def telegram(method, fields, voice=None):
    token,chat = os.environ.get('TELEGRAM_BOT_TOKEN'),os.environ.get('FINANCE_CHAT_ID')
    if not token or not chat: raise ValueError('Нужны TELEGRAM_BOT_TOKEN и FINANCE_CHAT_ID')
    fields = {**fields,'chat_id':chat}
    if voice is None:
        body = json.dumps(fields).encode()
        content_type = 'application/json'
    else:
        boundary = uuid.uuid4().hex
        parts = []
        for key,value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; filename="reply.ogg"\r\nContent-Type: audio/ogg\r\n\r\n'.encode()+voice+b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        body = b''.join(parts)
        content_type = f'multipart/form-data; boundary={boundary}'
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}',data=body,headers={'Content-Type':content_type})
    try:
        with urllib.request.urlopen(request,timeout=30) as response: value = json.load(response)
    except Exception:
        # HTTP exceptions include token-bearing URL. Do not log their representation.
        raise RuntimeError('Telegram delivery uncertain; inspect chat before retry') from None
    if not value.get('ok'): raise RuntimeError('Telegram rejected message')
    return value['result'].get('message_id')


def deliver(ledger, today, until, sender=None):
    pending = ledger.queue_reminders(today,until)
    if sender is None: return pending
    result = []
    for reminder in pending:
        # Atomic claim: two workers cannot both send. A crash after claim leaves
        # 'sending', deliberately requiring reconciliation instead of blind retry.
        with ledger.db:
            claimed = ledger.db.execute("UPDATE reminders SET state='sending',attempts=attempts+1 WHERE id=? AND state='pending'",(reminder['id'],)).rowcount
        if not claimed: continue
        try:
            message_id = sender(reminder['text'])
        except Exception:
            with ledger.db:
                ledger.db.execute("UPDATE reminders SET state='uncertain' WHERE id=?",(reminder['id'],))
            result.append({'id':reminder['id'],'state':'uncertain'})
            continue
        with ledger.db:
            ledger.db.execute("UPDATE reminders SET state='sent' WHERE id=?",(reminder['id'],))
            ledger.audit('reminder_sent',{'id':reminder['id'],'message_id':message_id})
        result.append({'id':reminder['id'],'state':'sent'})
    return result
