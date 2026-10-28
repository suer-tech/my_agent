"""Production readback, real EIS search and one labelled reminder delivery."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from local_finance.iva_telegram import EveBridge
from local_finance.personal_calendar import Calendar

DB=ROOT/'data/local-finance.sqlite3'
VAULT=ROOT/'data/iva-pilot/app/vault'
OUT=ROOT/'data/native-features-verification.json'
report={'started':time.time(),'cases':[]}
def save():
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    OUT.chmod(0o600)
def rows(table):
    with sqlite3.connect(DB) as c:
        return c.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
ops=rows('operations')
events=rows('personal_events')
memory={str(p.relative_to(VAULT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in VAULT.rglob('*.md') if '.git' not in p.parts}
report['before']={'operations':len(ops),'events':len(events),'memory_files':len(memory)}
save()
bridge=EveBridge(ROOT/'data/native-features-session',skip_daily=True)
def turn(name,text):
    start=time.monotonic()
    try:
        answer=bridge.send(text,'diagnostic:native-features:'+name+':'+str(time.time_ns()))
        item={'name':name,'completed':True,'answer':answer}
    except Exception as exc:
        item={'name':name,'completed':False,'error_type':type(exc).__name__}
    item['seconds']=round(time.monotonic()-start,1)
    report['cases'].append(item);save()
    print(name,item['completed'],flush=True)
    return item

cal=Calendar(DB)
try:
    # Authorized technical test through the same calendar backend. The running
    # Telegram dispatcher, not this script, must deliver it at the real time.
    added=cal.execute({'action':'add','title':'Тест после переключения: напоминания Ивы работают',
                       'after_minutes':1,'repeat':'once','kind':'event',
                       'lead_minutes':[0],'requires_ack':False},
                      source='diagnostic:native-features-reminder')
    report['test_event']=added;save()
    turn('memory','Проверь долговременную память через memory_search и чтение найденных карточек. Какие подтверждённые сведения обо мне и правила ведения финансов уже сохранены? Назови источники. Не записывай новые факты и не изменяй карточки. Ответ краткий.')
    turn('calendar','Вызови personal_calendar list и status. Назови мои активные напоминания и состояние диспетчера, включая тест после переключения. Ничего не создавай и не меняй.')
    turn('tenders','Найди сейчас через tender_agent до трёх актуальных подходящих мне тендеров в ЕИС на разработку или доработку сайтов, приложений, порталов или внедрение ИИ. Используй мой сохранённый профиль, не меняй его. Сделай один поисковый проход. Покажи ссылки, сроки, суммы, ограничения допуска и что не удалось проверить.')
    eid=added['event']['id']
    deadline=time.monotonic()+75
    while True:
        notices=cal.rows('SELECT state,message_id,due FROM personal_notices WHERE event_id=?',(eid,))
        if any(n['state'] in ('sent','uncertain') for n in notices) or time.monotonic()>deadline:break
        time.sleep(3)
    report['reminder_delivery']=notices
    report['calendar_status']=cal.status()
finally:
    if report.get('test_event'):
        cal.execute({'action':'cancel','id':report['test_event']['event']['id']},
                    source='diagnostic:native-features-cleanup')
    cal.close()
    current={r[0]:r for r in rows('personal_events')}
    report['preserved']={'operations_unchanged':digest(ops)==digest(rows('operations')),
        'existing_events_unchanged':all(current.get(r[0])==r for r in events),
        'original_memory_files_present':all((VAULT/p).is_file() for p in memory),
        'original_memory_files_unchanged':all((VAULT/p).is_file() and hashlib.sha256((VAULT/p).read_bytes()).hexdigest()==h for p,h in memory.items())}
    report['finished']=time.time();save()
