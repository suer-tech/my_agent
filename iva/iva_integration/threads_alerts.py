"""Threads incidents -> repair queue; only DM, lead and daily notices reach Iva.
No polling, no browser, no financial database access, no model dependency here.
"""
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import time

ROOT=Path('/opt/iva/data/threads-browser')


def category(error):
    text=str(error or '').lower()
    if any(x in text for x in ('publication','interrupted publication','перепроверьте отклик')):return 'publication'
    if any(x in text for x in ('verification','captcha','challenge')):return 'challenge'
    if any(x in text for x in ('restriction','temporarily blocked','rate limit')):return 'restriction'
    if any(x in text for x in ('login','account')):return 'login'
    if any(x in text for x in ('codex','ai_provider','ai provider','model')):return 'ai'
    if any(x in text for x in ('timeout','net::','connection','proxy','navigation')):return 'network'
    if any(x in text for x in ('editor','button','anchor','thread changed','comment failed')):return 'interface'
    return 'worker'


MESSAGES={
 'reporting':('Не удалось обновить статистику или ежедневный отчёт Threads.','Данные могут быть устаревшими. Нужно проверить базу статистики и threads-daily-report.timer/service.'),
 'messages':('Не удалось проверить личные сообщения Threads.','Нужно проверить вход в Messages или обновить чтение интерфейса; отсутствие новых сообщений не подтверждено.'),
 'login':('В серверном Threads требуется повторный вход.','Нужно войти в аккаунт suer_stuff в серверном браузере.'),
 'challenge':('Threads запросил проверку безопасности или CAPTCHA.','Нужно пройти проверку вручную в серверном браузере; автоматического обхода нет.'),
 'restriction':('Threads ограничил действия аккаунта.','Текущий обход остановлен. Проверь уведомление площадки; повторная отправка не запускается.'),
 'ai':('Не удалось проверить уместность обсуждения через AI.','Этот обход остановлен без новой публикации. Следующий обход попробует снова.'),
 'network':('Не удалось загрузить или проверить страницу Threads.','Проверь доступность сайта и серверного соединения. Результат возможной отправки смотри отдельно.'),
 'interface':('Не удалось однозначно найти нужные элементы Threads.','Автоматические клики остановлены; нужно проверить разметку страницы.'),
 'publication':('Результат отправки комментария не подтверждён.','Проверь ветку вручную. Эту попытку автоматически не повторяю.'),
 'worker':('Обход Threads завершился с ошибкой.','Нужна проверка состояния исполнителя; подтверждённой публикации по этой ошибке нет.'),
 'memory':('Серверному браузеру Threads не хватило памяти или он завершился из-за ограничения ресурсов.','Проверь нагрузку сервера и очередь MAX/Threads; второй браузер не запускается параллельно.'),
 'browser':('Серверный браузер Threads завершился до окончания задания.','Сохранённые результаты доступны. Неподтверждённые отправки автоматически не повторяются.'),
 'stalled':('Операция Threads превысила свой срок ожидания.','Проверяется внутренним монитором; подтверждённый сбой передаётся на исправление.'),
 'queue':('Threads ожидает браузер больше 20 минут.','Проверь очередь MAX/Threads: текущая операция может задерживать безопасную передачу.'),
 'schedule':('Расписание Threads выключено или пропустило ожидаемый запуск.','Проверь таймер и управляющий сервис.'),
 'coordinator':('Управляющий сервис заблокировал запуск Threads.','Проверь очередь и наличие другого владельца браузера; параллельный браузер не запускается.'),
 'like':('Комментарий опубликован, но состояние лайка не подтверждено.','Комментарий повторно не отправляю и кнопку лайка повторно не переключаю.'),
}


def load(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (FileNotFoundError,ValueError):return default


def detect(data,now,installed):
    launch=data.get('launch',{});report=data.get('report',{});coordinator=data.get('coordinator',{})
    research=coordinator.get('research') or {}
    research_paused=(coordinator.get('ok') is True and isinstance(research,dict)
                     and research.get('status')=='active' and research.get('granted') is True
                     and research.get('active') is True)
    cycle=report.get('cycle') or {};units=data.get('units',{});issues={}
    def add(key,code,url=None):issues[key]={'code':code,'url':url}
    for post in data.get('posts',[]):
        if post['state']=='uncertain':add('publication:'+post['url'],'publication',post['url'])
        elif post['state']=='sent' and post.get('liked')==-1 and (post.get('attempt_at') or 0)>=installed:add('like:'+post['url'],'like',post['url'])
    enabled=launch.get('enabled') and coordinator.get('enabled',True)
    if not enabled:return issues
    if cycle.get('state') in ('error','expired') and (cycle.get('finished') or 0)>=installed:
        add('cycle:'+category(cycle.get('error')),category(cycle.get('error')))
    if launch.get('publish',True) and units.get(data.get('timer_unit','threads-hourly.timer'),{}).get('ActiveState')!='active':add('schedule','schedule')
    requested=launch.get('last_requested_at') or launch.get('created_at') or now
    if launch.get('publish',True) and now-requested>data.get('interval',3600)+900 and now-installed>600:add('schedule','schedule')
    if launch.get('pending'):
        browser=units.get('threads-browser.service',{})
        if browser.get('Result')=='oom-kill':add('memory','memory')
        elif browser.get('ActiveState')=='failed':add('browser','browser')
        if coordinator.get('blocked'):add('coordinator','coordinator')
        if not research_paused and coordinator.get('current')=='threads':
            updated=max(report.get('updated_at',0),requested,cycle.get('started') or 0)
            activity=data.get('activity') or {}
            if activity.get('state')=='running':
                # Both campaigns share one browser. Waiting for the other campaign
                # or an inbox read is not evidence that this campaign is stalled.
                deadline=activity.get('deadline_at')
                owner=activity.get('campaign')
                if owner=='inbox':owner=data.get('inbox_campaign','extension')
                if owner==data.get('campaign','extension') and isinstance(deadline,(int,float)) and now>deadline+60:
                    add('stalled','stalled')
            elif now-updated>600:add('stalled','stalled')
            if browser.get('ActiveState')=='inactive' and now-requested>90:add('browser','browser')
        elif not research_paused and now-requested>1200:add('queue','queue')
        # A granted research lease deliberately keeps browsers stopped. Actual
        # OOM/failed-unit, publication, login and timer errors above remain visible.
        if not research_paused and data.get('memory_available_kb',9999999)<640*1024 and now-requested>600:add('memory','memory')
    return issues


class Monitor:
    def __init__(self,root=ROOT,clock=time.time,label="Threads",recover=True):
        self.label=label
        self.root=Path(root);self.clock=clock
        self.db=sqlite3.connect(self.root/'alerts.sqlite3');self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value REAL);
        CREATE TABLE IF NOT EXISTS incidents(key TEXT PRIMARY KEY,code TEXT,active INTEGER,first_seen REAL,last_seen REAL,url TEXT);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,text TEXT,state TEXT,message_id INTEGER,created REAL);
        ''')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('installed',?)",(clock(),))
            if recover:self.db.execute("UPDATE outbox SET state='uncertain' WHERE state='sending'")
            self.suppress_technical()
        self.installed=self.db.execute("SELECT value FROM meta WHERE key='installed'").fetchone()[0]
    def close(self):self.db.close()
    def suppress_technical(self):
        # Do not replay historical failures/recoveries after this policy change.
        # In-flight and uncertain deliveries remain untouched.
        self.db.execute("UPDATE outbox SET state='suppressed' WHERE state='pending' AND id NOT LIKE 'dm:%' AND id NOT LIKE 'daily:%' AND id NOT LIKE 'lead:%'")
    def sync(self,issues,notify_recovery=True):
        now=self.clock()
        with self.db:
            active={r['key']:r for r in self.db.execute('SELECT * FROM incidents WHERE active=1')}
            for key,item in issues.items():
                if key not in active:
                    self.db.execute('INSERT OR REPLACE INTO incidents VALUES (?,?,1,?,?,?)',(key,item['code'],now,now,item.get('url')))
                else:self.db.execute('UPDATE incidents SET last_seen=? WHERE key=?',(now,key))
            for key,previous in active.items():
                if key in issues:continue
                self.db.execute('UPDATE incidents SET active=0,last_seen=? WHERE key=?',(now,key))
            self.suppress_technical()
        self.save_status()
    def record_leads(self,posts):
        with self.db:
            for post in posts:
                if post['state']!='sent' or not post.get('reply_url') or (post.get('attempt_at') or 0)<self.installed:continue
                text=self.label+': нашла подходящий запрос на разработку и опубликовала предложение обсудить задачу.\nВетка: '+post['url']+'\nМой ответ: '+post['reply_url']
                self.db.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,'pending',NULL,?)",('lead:'+post['url'],text,self.clock()))

    def deliver(self,send,ready=lambda:True):
        with self.db:self.suppress_technical()
        row=self.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY created,rowid LIMIT 1").fetchone()
        if not row:return False
        try:
            if not ready():return False
        except Exception:return False # Known preflight failure, no send was attempted.
        with self.db:self.db.execute("UPDATE outbox SET state='sending' WHERE id=?",(row['id'],))
        try:
            result=send(row['text']);mid=result.get('message_id') if isinstance(result,dict) else result
            if not mid:raise RuntimeError('No delivery acknowledgement')
        except Exception:
            with self.db:self.db.execute("UPDATE outbox SET state='uncertain' WHERE id=?",(row['id'],))
        else:
            with self.db:self.db.execute("UPDATE outbox SET state='sent',message_id=? WHERE id=?",(mid,row['id']))
        self.save_status();return True
    def save_status(self):
        value={'updated_at':self.clock(),'active':[dict(x) for x in self.db.execute('SELECT key,code,first_seen,last_seen,url FROM incidents WHERE active=1')],
               'delivery':{r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM outbox GROUP BY state')},
               'technical_notifications':False,'incident_destination':'codex_repair_queue'}
        tmp=self.root/'alerts-status.tmp';tmp.write_text(json.dumps(value,ensure_ascii=False));tmp.chmod(0o600);tmp.replace(self.root/'alerts-status.json')


def recovery_proven(code,cycle,preflight,incident_at):
    if code=='interface':return preflight.get('verified_at',0)>incident_at
    if code=='ai':return cycle.get('state') in ('done','preview') and cycle.get('inspected',0)>0
    return cycle.get('state') in ('done','preview')


def coordinator_status():
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(6);client.connect('/run/iva-browser/control.sock')
            client.sendall(b'{"action":"status"}\n');result=b''
            while b'\n' not in result:
                chunk=client.recv(65536)
                if not chunk:raise RuntimeError('Coordinator disconnected')
                result+=chunk
            return json.loads(result.split(b'\n',1)[0])
    except (OSError,ValueError,RuntimeError):return {'blocked':'Coordinator unavailable','enabled':True}


def collect(root=ROOT):
    root=Path(root);data={'launch':load(root/'hourly-launch.json',{}),'report':load(root/'hourly-status.json',{}),'coordinator':coordinator_status(),'units':{},'posts':[]}
    data['timer_unit']='threads-development.timer' if root.name=='development' else 'threads-hourly.timer'
    data['interval']=1800 if root.name=='development' else 3600
    data['campaign']='development' if root.name=='development' else 'extension'
    data['activity']=load(ROOT/'activity.json',{})
    data['inbox_campaign']='extension' if load(ROOT/'hourly-launch.json',{}).get('pending') else 'development'
    names=['threads-browser.service',data['timer_unit']]
    if root==ROOT:names+=['threads-daily-report.service','threads-daily-report.timer']
    for name in names:
        r=subprocess.run(['systemctl','show',name,'--property=ActiveState,Result,ExecMainStatus'],capture_output=True,text=True,timeout=8)
        data['units'][name]=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
    db_path=root/'hourly.sqlite3'
    if db_path.exists():
        with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            data['posts']=[dict(r) for r in db.execute("SELECT url,state,liked,attempt_at,reply_url FROM posts WHERE state IN ('uncertain','sent')")]
    mem=Path('/proc/meminfo').read_text()
    data['memory_available_kb']=next(int(x.split()[1]) for x in mem.splitlines() if x.startswith('MemAvailable:'))
    return data


def main():
    import fcntl
    from local_finance.bot import Telegram,OWNER,load_env
    from iva_integration.run_pilot import env
    with (ROOT/'alerts.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        os.environ.update(env());load_env(Path('/opt/iva/.env'))
        if os.environ.get('FINANCE_ALLOWED_USER_ID')!=str(OWNER):raise RuntimeError('Owner configuration mismatch')
        telegram=Telegram(os.environ['TELEGRAM_BOT_TOKEN'])
        if not os.environ.get('TELEGRAM_HTTPS_PROXY'):raise RuntimeError('Iva Telegram proxy required')
        from iva_integration.threads_reporting import refresh
        statistics_error=False
        try:refresh(ROOT)
        except (sqlite3.Error,OSError,ValueError):statistics_error=True
        for root,label in [(ROOT,'Threads — расширение'),(ROOT/'development','Threads — поиск заказов')]:
            if not root.is_dir():continue
            monitor=Monitor(root=root,label=label)
            try:
                data=collect(root);issues=detect(data,time.time(),monitor.installed)
                cycle=data['report'].get('cycle') or {}
                preflight=load(ROOT/'editor-preflight-status.json',{})
                for row in monitor.db.execute('SELECT key,code,url,first_seen FROM incidents WHERE active=1'):
                    if row['key'].startswith('cycle:') and not recovery_proven(row['code'],cycle,preflight,row['first_seen']):
                        issues.setdefault(row['key'],{'code':row['code'],'url':row['url']})
                if root==ROOT and (statistics_error or data['units'].get('threads-daily-report.service',{}).get('Result') not in (None,'success') or data['units'].get('threads-daily-report.timer',{}).get('ActiveState')!='active'):
                    issues['reporting:status']={'code':'reporting'}
                if root==ROOT and load(ROOT/'inbox-status.json',{}).get('state')=='error':
                    issues['inbox:messages']={'code':'messages'}
                monitor.sync(issues,notify_recovery=bool(data['launch'].get('enabled') and data['coordinator'].get('enabled',True)))
                from iva_integration.threads_repair import observe
                inbox=load(ROOT/'inbox-status.json',{})
                observe('development' if root.name=='development' else 'extension',issues,{
                    'checked_at':time.time(),'cycle':cycle,
                    'inbox_checked_at':inbox.get('checked_at'),
                    'inbox':{k:inbox[k] for k in ('state','error','error_code','folder','stage','attempt') if k in inbox},
                    'operation_deadline':data.get('activity',{}).get('deadline_at'),
                    'activity':data.get('activity',{}),
                    'units':data.get('units',{}),
                    'posts':[p for p in data.get('posts',[]) if p['url'] in {item.get('url') for item in issues.values()}][:10],
                })
                if root.name=='development':monitor.record_leads(data['posts'])
                for _ in range(5):
                    if not monitor.deliver(lambda text:telegram.send(OWNER,text),lambda:bool(telegram.call('getMe',{}))):break
                monitor.save_status()
            finally:monitor.close()


if __name__=='__main__':main()
