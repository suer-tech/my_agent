"""Owner's local calendar and durable Telegram reminder outbox (no model at delivery)."""
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib
import json
import sqlite3
import threading
import time

ZONE=timezone(timedelta(hours=5))
def now():return datetime.now(ZONE).replace(microsecond=0)
def iso(dt):return dt.astimezone(ZONE).isoformat(timespec='seconds')
def moment(value):
    dt=datetime.fromisoformat(value)
    return dt.replace(tzinfo=ZONE) if dt.tzinfo is None else dt.astimezone(ZONE)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()[:20]

class Calendar:
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None);self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS personal_events(id TEXT PRIMARY KEY,title TEXT NOT NULL,at TEXT NOT NULL,repeat TEXT NOT NULL,kind TEXT NOT NULL,leads TEXT NOT NULL,ack INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS personal_occurrences(event_id TEXT NOT NULL,at TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(event_id,at));
        CREATE TABLE IF NOT EXISTS personal_notices(id TEXT PRIMARY KEY,event_id TEXT NOT NULL,occurrence TEXT NOT NULL,due TEXT NOT NULL,state TEXT NOT NULL,text TEXT NOT NULL,message_id INTEGER,error TEXT);
        CREATE INDEX IF NOT EXISTS personal_notices_due ON personal_notices(state,due);
        CREATE TABLE IF NOT EXISTS personal_mutations(source TEXT NOT NULL,key TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(source,key));
        CREATE TABLE IF NOT EXISTS personal_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS personal_reviews(day TEXT PRIMARY KEY,state TEXT NOT NULL,message_id INTEGER,error TEXT);
        ''')
    def close(self):self.db.close()
    def rows(self,sql,args=()):return [dict(r) for r in self.db.execute(sql,args)]
    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield
        except BaseException:self.db.rollback();raise
        else:self.db.commit()
    def occurrence(self,e,index):
        anchor=moment(e['at']);repeat=e['repeat']
        if repeat=='once':return anchor if index==0 else None
        if repeat in ('daily','weekly'):return anchor+timedelta(days=index*(1 if repeat=='daily' else 7))
        months=index*(12 if repeat=='yearly' else 1);year=anchor.year+(anchor.month-1+months)//12;month=(anchor.month-1+months)%12+1
        return anchor.replace(year=year,month=month,day=min(anchor.day,monthrange(year,month)[1]))
    def expand(self,until,clock=None):
        clock=clock or now()
        for e in self.rows('SELECT * FROM personal_events WHERE active=1'):
            candidates=[];previous=None
            for index in range(36600):
                when=self.occurrence(e,index)
                if when is None or when>until:break
                if when<moment(e['created']):continue
                if when<clock:previous=when
                else:candidates.append(when)
            if previous:candidates.insert(0,previous)
            for when in candidates:
                occurrence=iso(when)
                self.db.execute('INSERT INTO personal_occurrences VALUES(?,?,0) ON CONFLICT(event_id,at) DO UPDATE SET done=CASE WHEN personal_occurrences.done=2 THEN 0 ELSE personal_occurrences.done END',(e['id'],occurrence))
                done=self.rows('SELECT done FROM personal_occurrences WHERE event_id=? AND at=?',(e['id'],occurrence))[0]['done']
                if done:continue
                offsets=[(-int(lead),'scheduled') for lead in json.loads(e['leads'])]
                if e['ack']:offsets += [(day*1440,'overdue') for day in (1,2,3)]
                for offset,kind in offsets:
                    due=iso(when+timedelta(minutes=offset));nid=digest(e['id']+occurrence+str(offset))
                    label='Просрочено, выполнение не отмечено' if kind=='overdue' else 'Напоминание'
                    text=f"{label}: {e['title']}\nДата события: {when.strftime('%d.%m.%Y %H:%M')} (Екатеринбург)."
                    if when<clock and kind=='scheduled':text+='\nСрок уже наступил.'
                    self.db.execute("INSERT INTO personal_notices(id,event_id,occurrence,due,state,text) VALUES(?,?,?,?,'pending',?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,due=excluded.due,state=CASE WHEN personal_notices.state='cancelled' THEN 'pending' ELSE personal_notices.state END",(nid,e['id'],occurrence,due,text))
    def status(self):
        tick=self.rows("SELECT value FROM personal_meta WHERE key='tick'")
        last=tick[0]['value'] if tick else None
        return {'now':iso(now()),'timezone':'Asia/Yekaterinburg','last_tick':last,'dispatcher_alive':bool(last and (now()-moment(last)).total_seconds()<90),'delivery_states':self.rows('SELECT state,count(*) count FROM personal_notices GROUP BY state'),'uncertain':self.rows("SELECT id,text,due,error FROM personal_notices WHERE state='uncertain'"),'daily_reviews':self.rows('SELECT * FROM personal_reviews ORDER BY day DESC LIMIT 3'),'dispatcher_errors':self.rows("SELECT value FROM personal_meta WHERE key='last_error'")}
    def execute(self,data,source='local',clock=None):
        clock=clock or now();action=data['action']
        if action=='status':return {'ok':True,**self.status()}
        if action=='list':
            start=moment(data.get('start',iso(clock.replace(hour=0,minute=0,second=0))))
            end=moment(data.get('end',iso(start+timedelta(days=31))))
            if end<start or end-start>timedelta(days=366):raise ValueError('Период календаря — до 366 дней')
            with self.transaction():self.expand(end,clock)
            return {'ok':True,'events':self.rows('SELECT * FROM personal_events WHERE active=1'),'occurrences':self.rows('SELECT o.*,e.title,e.kind FROM personal_occurrences o JOIN personal_events e ON e.id=o.event_id WHERE e.active=1 AND o.at BETWEEN ? AND ? ORDER BY o.at LIMIT 200',(iso(start),iso(end))),'notices':self.rows("SELECT n.id notice_id,n.event_id,n.occurrence,n.due,n.state,e.title FROM personal_notices n JOIN personal_events e ON e.id=n.event_id WHERE e.active=1 AND n.state NOT IN ('cancelled','superseded') ORDER BY n.due DESC LIMIT 100"),'overdue_unfinished':self.rows('SELECT o.*,e.title FROM personal_occurrences o JOIN personal_events e ON e.id=o.event_id WHERE e.active=1 AND e.ack=1 AND o.done=0 AND o.at<? ORDER BY o.at LIMIT 50',(iso(clock),)),'scheduler':self.status()}
        key=digest(json.dumps(data,sort_keys=True,ensure_ascii=False))
        with self.transaction():
            old=self.rows('SELECT result FROM personal_mutations WHERE source=? AND key=?',(source,key))
            if old:return {**json.loads(old[0]['result']),'duplicate':True}
            if action in ('add','update'):
                previous=self.rows('SELECT * FROM personal_events WHERE id=?',(data.get('id'),)) if action=='update' else []
                if action=='update' and not previous:raise ValueError('Событие не найдено')
                p=previous[0] if previous else {}
                title=data.get('title',p.get('title','')).strip()
                if not 1<=len(title)<=500:raise ValueError('Название события: 1–500 символов')
                if 'after_minutes' in data:
                    minutes=data['after_minutes']
                    if type(minutes)!=int or not 0<=minutes<=525600:raise ValueError('Интервал — 0–525600 минут')
                    when=clock+timedelta(minutes=minutes)
                else:when=moment(data.get('at',p.get('at','')))
                repeat=data.get('repeat',p.get('repeat','once'));kind=data.get('kind',p.get('kind','reminder'))
                if repeat not in ('once','daily','weekly','monthly','yearly') or kind not in ('reminder','birthday','event','deadline'):raise ValueError('Неизвестный тип или повторение')
                if kind=='birthday':repeat='yearly'
                if repeat=='once' and when<clock-timedelta(seconds=10):raise ValueError('Дата уже прошла. Уточните новую дату.')
                anchor=iso(when);index=0
                while repeat!='once' and when<clock:
                    index+=1;when=self.occurrence({'at':anchor,'repeat':repeat},index)
                leads=data.get('lead_minutes',json.loads(p['leads']) if p else [1440] if kind=='birthday' else [0])
                if not isinstance(leads,list) or not leads or len(leads)>5 or any(type(x)!=int or not 0<=x<=43200 for x in leads):raise ValueError('От 1 до 5 уведомлений, за 0–43200 минут')
                ack=data.get('requires_ack',bool(p['ack']) if p else kind in ('reminder','deadline'))
                eid=p.get('id') or digest(title.casefold()+iso(when)+repeat+kind)
                stored_at=anchor if repeat!='once' else iso(when)
                self.db.execute('INSERT INTO personal_events VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,at=excluded.at,repeat=excluded.repeat,kind=excluded.kind,leads=excluded.leads,ack=excluded.ack,active=1',(eid,title,stored_at,repeat,kind,json.dumps(sorted(set(leads))),int(ack),iso(clock)))
                if action=='update':
                    self.db.execute("UPDATE personal_notices SET state='cancelled' WHERE event_id=? AND state IN ('pending','uncertain')",(eid,))
                    self.db.execute('UPDATE personal_occurrences SET done=2 WHERE event_id=? AND done=0',(eid,))
                result={'ok':True,'event':self.rows('SELECT * FROM personal_events WHERE id=?',(eid,))[0],'next_occurrence_at':iso(when),'next_notification_at':iso(max(clock,when-timedelta(minutes=max(leads)))),'scheduler':self.status(),'timezone':'Asia/Yekaterinburg','note':'Создано в локальном календаре. Важные напоминания повторяются через 1, 2 и 3 дня после срока, пока выполнение не отмечено; затем остаются в ежедневном контроле.' if ack else 'Создано в локальном календаре.'}
            elif action=='cancel':
                eid=data['id']
                if not self.rows('SELECT id FROM personal_events WHERE id=?',(eid,)):raise ValueError('Событие не найдено')
                self.db.execute('UPDATE personal_events SET active=0 WHERE id=?',(eid,));self.db.execute("UPDATE personal_notices SET state='cancelled' WHERE event_id=? AND state IN ('pending','uncertain')",(eid,));result={'ok':True,'cancelled':eid}
            elif action in ('done','snooze'):
                notice=self.rows('SELECT * FROM personal_notices WHERE id=?',(data.get('notice_id'),))
                if not notice:raise ValueError('Напоминание не найдено: нужен notice_id из календаря/уведомления')
                n=notice[0]
                if action=='done':
                    self.db.execute('UPDATE personal_occurrences SET done=1 WHERE event_id=? AND at=?',(n['event_id'],n['occurrence']))
                    self.db.execute("UPDATE personal_notices SET state='cancelled' WHERE event_id=? AND occurrence=? AND state IN ('pending','uncertain')",(n['event_id'],n['occurrence']));result={'ok':True,'done':n['occurrence']}
                else:
                    minutes=data.get('minutes',60)
                    if type(minutes)!=int or not 1<=minutes<=43200:raise ValueError('Отложить можно на 1–43200 минут')
                    if self.rows('SELECT done FROM personal_occurrences WHERE event_id=? AND at=?',(n['event_id'],n['occurrence']))[0]['done']:raise ValueError('Выполнение уже отмечено')
                    due=iso(clock+timedelta(minutes=minutes));nid=digest(source+key)
                    self.db.execute("UPDATE personal_notices SET state='superseded' WHERE event_id=? AND occurrence=? AND state='pending' AND due<=?",(n['event_id'],n['occurrence'],due))
                    self.db.execute("INSERT OR IGNORE INTO personal_notices(id,event_id,occurrence,due,state,text) VALUES(?,?,?,?,'pending',?)",(nid,n['event_id'],n['occurrence'],due,n['text']))
                    result={'ok':True,'notice_id':nid,'due':due}
            else:raise ValueError('Неизвестное действие календаря')
            self.db.execute('INSERT INTO personal_mutations VALUES(?,?,?)',(source,key,json.dumps(result,ensure_ascii=False)))
            return result
    def recover(self):
        self.db.execute("UPDATE personal_notices SET state='uncertain',error='Process stopped during delivery; check Telegram before retry' WHERE state='sending'")
        self.db.execute("UPDATE personal_reviews SET state='uncertain',error='Process stopped during delivery' WHERE state='sending'")
    def tick(self,api,owner,clock=None):
        clock=clock or now()
        with self.transaction():
            self.expand(clock+timedelta(days=7),clock)
            self.db.execute("INSERT OR REPLACE INTO personal_meta VALUES('tick',?)",(iso(clock),))
            # Catch up once per event, merging notifications missed while offline.
            due=self.rows("SELECT n.* FROM personal_notices n JOIN personal_events e ON e.id=n.event_id JOIN personal_occurrences o ON o.event_id=n.event_id AND o.at=n.occurrence WHERE n.state='pending' AND n.due<=? AND e.active=1 AND o.done=0 ORDER BY n.occurrence DESC,n.due DESC",(iso(clock),))
            selected={}
            for n in due:
                if n['event_id'] in selected:self.db.execute("UPDATE personal_notices SET state='superseded' WHERE id=?",(n['id'],))
                else:selected[n['event_id']]=n
        for n in selected.values():
            with self.transaction():
                claimed=self.db.execute("UPDATE personal_notices SET state='sending' WHERE id=? AND state='pending' AND EXISTS(SELECT 1 FROM personal_events WHERE id=? AND active=1) AND EXISTS(SELECT 1 FROM personal_occurrences WHERE event_id=? AND at=? AND done=0)",(n['id'],n['event_id'],n['event_id'],n['occurrence'])).rowcount
            if not claimed:continue
            try:
                result=api.call('sendMessage',{'chat_id':owner,'text':n['text'],'reply_markup':{'inline_keyboard':[[{'text':'Готово','callback_data':'cal:done:'+n['id']},{'text':'Через час','callback_data':'cal:later:'+n['id']}]]}})
                self.db.execute("UPDATE personal_notices SET state='sent',message_id=? WHERE id=?",(result.get('message_id'),n['id']))
            except Exception:
                self.db.execute("UPDATE personal_notices SET state='uncertain',error='Telegram delivery not confirmed; check chat' WHERE id=?",(n['id'],))
        if clock.hour>=9:
            overdue=self.rows('SELECT e.title,o.at FROM personal_occurrences o JOIN personal_events e ON e.id=o.event_id WHERE e.active=1 AND e.ack=1 AND o.done=0 AND o.at<? ORDER BY o.at LIMIT 15',(iso(clock-timedelta(days=1)),))
            uncertain=self.rows("SELECT id FROM personal_notices WHERE state='uncertain'")
            if overdue or uncertain:
                claimed=self.db.execute("INSERT OR IGNORE INTO personal_reviews(day,state) VALUES(?,'sending')",(clock.date().isoformat(),)).rowcount
                if claimed:
                    text='Контроль календаря: не отмечено выполнение:\n'+'\n'.join('- '+r['title']+' — '+moment(r['at']).strftime('%d.%m %H:%M') for r in overdue)
                    if uncertain:text+=f'\nУ {len(uncertain)} уведомлений доставка не подтверждена. Попросите показать статус календаря.'
                    try:
                        sent=api.call('sendMessage',{'chat_id':owner,'text':text+'\nМожно отметить выполненное или перенести срок.'})
                        self.db.execute("UPDATE personal_reviews SET state='sent',message_id=? WHERE day=?",(sent.get('message_id'),clock.date().isoformat()))
                    except Exception:self.db.execute("UPDATE personal_reviews SET state='uncertain',error='Telegram delivery not confirmed' WHERE day=?",(clock.date().isoformat(),))

def start_dispatcher(path,api,owner,stop_file):
    def run():
        store=Calendar(path);store.recover()
        try:
            while not stop_file.exists():
                try:
                    store.tick(api,owner)
                    store.db.execute("DELETE FROM personal_meta WHERE key='last_error'")
                except Exception as error:
                    try:store.db.execute("INSERT OR REPLACE INTO personal_meta VALUES('last_error',?)",(type(error).__name__,))
                    except Exception:pass # Last successful tick stays visible in status.
                for _ in range(5):
                    if stop_file.exists():return
                    time.sleep(1)
        finally:store.close()
    thread=threading.Thread(target=run,name='iva-calendar-dispatcher',daemon=True);thread.start();return thread
