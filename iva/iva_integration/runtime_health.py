"""Independent lightweight liveness and repeated-reply-failure notifications."""
from contextlib import closing
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request
from iva_integration.runtime_diagnostics import ROOT,event


def issues(now,telegram_status,scheduler_heartbeat,primary_states,services):
    result={}
    def age(value):
        try:return now-datetime.fromisoformat(value).timestamp()
        except (ValueError,TypeError):return float('inf')
    limit=660 if telegram_status.get('state')=='processing' else 180
    if age(telegram_status.get('heartbeat'))>limit:result['telegram']={'code':'telegram_health'}
    if age(scheduler_heartbeat)>120:result['scheduler']={'code':'scheduler_health'}
    if len(primary_states)>=3 and all(x=='failed' for x in primary_states[:3]):result['replies']={'code':'reply_health'}
    for name,healthy in services.items():
        if not healthy:result['service:'+name]={'code':'reply_health'}
    return result


def main():
    import fcntl
    from iva_integration.threads_alerts import Monitor,MESSAGES
    from iva_integration.run_pilot import env
    from local_finance.bot import Telegram,OWNER,load_env
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (ROOT/'health.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=Path('/opt/iva/data/iva-telegram/status.json')
        try:telegram_status=json.loads(path.read_text())
        except (OSError,ValueError):telegram_status={}
        with closing(sqlite3.connect('file:/opt/iva/data/local-finance.sqlite3?mode=ro',uri=True)) as db:
            row=db.execute("SELECT value FROM scheduled_meta WHERE key='heartbeat'").fetchone()
            heartbeat=row[0] if row else None
            mail_jobs=[]
            for job,state,next_at,cursor,failures in db.execute("SELECT id,state,next_at,cursor,failures FROM scheduled_jobs WHERE json_extract(spec,'$.kind')='mail_watch' AND state!='cancelled'"):
                mail_jobs.append({'id':job,'state':state,'next_at':next_at,'last_uid':json.loads(cursor).get('last_uid'),'failures':failures})
            provider=db.execute("SELECT value FROM local_meta WHERE key='opencode_retry_after'").fetchone()
            degraded_until=float(provider[0]) if provider else None
            states=[r[0] for r in db.execute('SELECT state FROM local_primary_turns ORDER BY rowid DESC LIMIT 3')]
        config=json.loads(Path('/opt/iva/data/iva-pilot/control.json').read_text());services={}
        for name,key,path in [('gateway','gateway_port','/health'),('engine','iva_port','/')]:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{config[key]}{path}',timeout=3) as r:services[name]=r.status==200
            except OSError:services[name]=False
        found=issues(time.time(),telegram_status,heartbeat,states,services)
        MESSAGES.update({
         'telegram_health':('Ива давно не обновляла состояние Telegram.','Нужна проверка процесса; сообщения автоматически не повторяю.'),
         'scheduler_health':('Планировщик Ивы перестал обновлять heartbeat.','Проверки почты могут задерживаться. Нужна проверка сервиса.'),
         'reply_health':('Повторяются сбои ответов Ивы или недоступен её рабочий сервис.','Причины сохранены в runtime-diagnostics и turn-diagnostics. Запросы с возможными действиями не повторяю.')})
        monitor=Monitor(root=ROOT,label='Ива')
        try:
            monitor.sync(found,notify_recovery=False)
            os.environ.update(env());load_env(Path('/opt/iva/.env'))
            if os.environ.get('FINANCE_ALLOWED_USER_ID')!=str(OWNER) or not os.environ.get('TELEGRAM_HTTPS_PROXY'):raise RuntimeError('Private Telegram routing required')
            telegram=Telegram(os.environ['TELEGRAM_BOT_TOKEN'])
            for _ in range(3):
                if not monitor.deliver(lambda text:telegram.send(OWNER,text),lambda:bool(telegram.call('getMe',{}))):break
            value={'checked_at':time.time(),'issues':list(found),'services':services,'telegram_state':telegram_status.get('state'),'mail_jobs':mail_jobs,'opencode_retry_after':degraded_until,'opencode_degraded':bool(degraded_until and degraded_until>time.time())}
            p=ROOT/'health-status.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value));tmp.chmod(0o600);tmp.replace(p)
            event('health',stage='monitor',state='error' if found else 'ok',count=len(found))
        finally:monitor.close()


if __name__=='__main__':main()
