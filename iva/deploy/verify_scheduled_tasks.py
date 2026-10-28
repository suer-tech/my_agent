"""Live scheduler smoke check: isolated diagnostic Iva session and cancellable test jobs."""
from pathlib import Path
import json
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from iva_integration.scheduled_tasks import Store,stamp,now
from iva_integration.scheduled_worker import poll_mail
from iva_integration.run_pilot import env
from local_finance.iva_telegram import EveBridge
import os

def main():
    os.environ.update(env());folder=ROOT/'data/scheduled-release-20261022';report={};store=Store(ROOT/'data/local-finance.sqlite3');ids=[]
    try:
        # This developer test is explicitly labelled, never impersonates an owner Telegram envelope.
        mail={'action':'create','title':'Техническая проверка почты','task':'Проверка подключения, только новые письма от тестового несуществующего отправителя','kind':'mail_watch','queries':['iva-scheduler-nonexistent-sender-20261022.invalid'],'interval_minutes':5}
        a=store.execute(mail,'diagnostic:scheduled-mail:20261022',poll_mail);job=a['job']['id'];ids.append(job)
        store.db.execute('UPDATE scheduled_jobs SET next_at=? WHERE id=?',(stamp(now()),job))
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            runs=store.rows('SELECT state,error FROM scheduled_runs WHERE job_id=? ORDER BY started DESC LIMIT 1',(job,))
            if runs and runs[0]['state']!='running':report['mail']=runs[0];break
            time.sleep(2)
        if report.get('mail',{}).get('state')!='done':raise RuntimeError('Live mail schedule failed')
        store.execute({'action':'cancel','id':job},'diagnostic:scheduled-mail:cancel')
        agent={'action':'create','title':'Техническая проверка расписания','task':'Это техническая проверка планировщика. Ничего не сохраняй и не вызывай инструменты. Заверши с notify=true и text="Проверка Ивы: задача по расписанию выполнена, уведомление в Telegram работает."','kind':'agent','interval_minutes':5,'notification':'always'}
        b=store.execute(agent,'diagnostic:scheduled-agent:20261022');job=b['job']['id'];ids.append(job)
        store.db.execute('UPDATE scheduled_jobs SET next_at=? WHERE id=?',(stamp(now()),job))
        deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            notices=store.rows('SELECT state,message_id,error FROM scheduled_outbox WHERE job_id=?',(job,))
            if notices and notices[0]['state'] in ('sent','uncertain'):report['telegram']=notices[0];break
            time.sleep(3)
        if report.get('telegram',{}).get('state')!='sent':raise RuntimeError('Live scheduler Telegram delivery not confirmed')
        store.execute({'action':'cancel','id':job},'diagnostic:scheduled-agent:cancel')
        answer=EveBridge(folder/'diagnostic-session',skip_daily=True).send('Техническая проверка нового инструмента. Вызови scheduled_tasks status и list. Не создавай, не изменяй задач и ничего не сохраняй в память. Сообщи, работает ли исполнитель расписаний и есть ли активные задачи. Это отдельная проверка, а не данные о владельце.','diagnostic:scheduled-readback:20261022')
        report['iva']=answer;report['status']=store.status();report['passed']=True
    finally:
        for job in ids:
            store.execute({'action':'cancel','id':job},'diagnostic:scheduled-cleanup:'+job)
        (folder/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));store.close()
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
