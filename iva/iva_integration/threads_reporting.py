"""Read-only comment accounting and durable daily reports for Iva."""
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path('/opt/iva/data/threads-browser')
TZ=timezone(timedelta(hours=5),name='Asia/Yekaterinburg')


def rows(path,query):
    if not path.exists():return []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        return [dict(r) for r in db.execute(query)]


def summarize(root=ROOT,start=0,end=None):
    root=Path(root);end=time.time() if end is None else end
    result={'confirmed':0,'uncertain':0,'sending':0,'error_cycles':0,'campaigns':{},'recent':[]}
    seen=set()
    for name,directory in [('extension',root),('development',root/'development')]:
        posts=rows(directory/'hourly.sqlite3','SELECT url,state,reply_url,attempt_at FROM posts')
        counts={'confirmed':0,'uncertain':0,'sending':0,'error_cycles':0}
        for p in posts:
            if not start <= (p['attempt_at'] or 0) < end:continue
            key=p['reply_url'] or p['url']
            if key in seen:continue
            state=p['state'];field='confirmed' if state=='sent' and p['reply_url'] else ('uncertain' if state=='sent' else state)
            if field not in ('confirmed','uncertain','sending'):continue
            seen.add(key);counts[field]+=1
            result['recent'].append({'campaign':name,**p})
        cycles=rows(directory/'hourly.sqlite3','SELECT state,finished FROM cycles')
        counts['error_cycles']=sum(1 for c in cycles if c['state'] in ('error','expired') and start <= (c['finished'] or 0) < end)
        result['campaigns'][name]=counts
        for key,value in counts.items():result[key]+=value
    # The original pilot has no trustworthy timestamp. Keep it separate from dated totals.
    legacy=rows(root/'promotion.sqlite3','SELECT state,post_url,result FROM runs')
    result['legacy_confirmed_without_timestamp']=len({r['post_url'] for r in legacy if r['state']=='sent' and json.loads(r['result'] or '{}').get('links') and r['post_url'] not in {p['url'] for p in result['recent']}})
    result['lifetime_confirmed_including_legacy']=result['confirmed']+result['legacy_confirmed_without_timestamp'] if start==0 else None
    result['recent']=sorted(result['recent'],key=lambda p:p['attempt_at'] or 0,reverse=True)[:10]
    result.update(period_start=start,period_end=end)
    return result


def refresh(root=ROOT,now=None):
    now=time.time() if now is None else now
    midnight=datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    value={'updated_at':now,'timezone':'Asia/Yekaterinburg','today':summarize(root,midnight,now),
           'all':summarize(root,0,now),'daily_report_time':'21:00','daily_report_period':'previous 24 hours'}
    path=Path(root)/'statistics.json';tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');tmp.chmod(0o600);tmp.replace(path)
    return value


def daily(root=ROOT,now=None,enqueue=True):
    from iva_integration.threads_alerts import Monitor
    now=time.time() if now is None else now
    end=datetime.fromtimestamp(now,TZ).replace(hour=21,minute=0,second=0,microsecond=0)
    if end.timestamp()>now:end-=timedelta(days=1)
    start=end-timedelta(days=1);s=summarize(root,start.timestamp(),end.timestamp())
    text='Threads — ежедневный отчёт\n'+start.strftime('%d.%m %H:%M')+' — '+end.strftime('%d.%m %H:%M')+' (Екатеринбург)\n'
    for name,label in [('extension','Расширение'),('development','Поиск заказов')]:
        c=s['campaigns'][name]
        text+=f"{label}: подтверждено {c['confirmed']}, без подтверждения {c['uncertain']}, в отправке {c['sending']}, обходов с ошибками {c['error_cycles']}.\n"
    active=0
    for directory in (Path(root),Path(root)/'development'):
        try:active+=len(json.loads((directory/'alerts-status.json').read_text(encoding='utf-8')).get('active',[]))
        except (FileNotFoundError,ValueError):pass
    text+='Всего подтверждено: '+str(s['confirmed'])+'. Активных предупреждений: '+str(active)+'.'
    if not active and not s['error_cycles'] and not s['uncertain'] and not s['sending']:text+=' Ошибок отправки за период не зафиксировано.'
    if enqueue:
        monitor=Monitor(root=root,recover=False)
        try:
            with monitor.db:monitor.db.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,'pending',NULL,?)",('daily:'+end.date().isoformat(),text,now))
        finally:monitor.close()
    return text


if __name__=='__main__':
    import sys
    if '--daily' in sys.argv:
        daily(enqueue='--preview' not in sys.argv)
    else:refresh()
