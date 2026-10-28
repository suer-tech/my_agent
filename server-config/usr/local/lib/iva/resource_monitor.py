"""Deterministic Linux resource monitor. No model/agent/token calls."""
from datetime import datetime,timezone,timedelta
import json
import os
from pathlib import Path
import shutil
import time
import urllib.request

MIB=1024**2;GIB=1024**3
ROOT=Path('/opt/iva')
STATE=Path('/var/lib/iva-resource-monitor/state.json')
RUNTIME=Path('/run/iva-resource-monitor/state.json')
OWNER=412850740


def sample():
    disk=shutil.disk_usage(ROOT)
    memory={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:','SwapTotal:','SwapFree:'))}
    return {'disk_total':disk.total,'disk_free':disk.free,'memory_total':memory['MemTotal'],'memory_available':memory['MemAvailable'],'swap_total':memory.get('SwapTotal',0),'swap_free':memory.get('SwapFree',0)}


def thresholds(metrics):
    return {'disk':{'warning':max(GIB,int(metrics['disk_total']*.10)),'critical':max(512*MIB,int(metrics['disk_total']*.05)),'margin':256*MIB},
            'memory':{'warning':256*MIB,'critical':128*MIB,'margin':64*MIB}}


def level(free,limits,previous='normal'):
    if free<limits['critical']:return 'critical'
    if previous=='critical' and free<limits['critical']+limits['margin']//2:return 'critical'
    if free<limits['warning']:return 'warning'
    if previous in ('critical','warning') and free<limits['warning']+limits['margin']:return 'warning'
    return 'normal'


def save(path,value):
    temp=path.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    temp.replace(path)


def send(text):
    # Read only the existing Telegram transport settings; do not load model code.
    settings={}
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        key,sep,value=line.partition('=')
        if sep and key.strip() in ('TELEGRAM_BOT_TOKEN','TELEGRAM_HTTPS_PROXY'):settings[key.strip()]=value.strip()
    token=settings.get('TELEGRAM_BOT_TOKEN')
    if not token:raise RuntimeError('Telegram configuration unavailable')
    proxy=settings.get('TELEGRAM_HTTPS_PROXY','')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy}) if proxy else urllib.request.ProxyHandler({}))
    body=json.dumps({'chat_id':OWNER,'text':text}).encode()
    request=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=body,headers={'Content-Type':'application/json'})
    try:
        with opener.open(request,timeout=20) as response:result=json.load(response)
        if not result.get('ok') or not result.get('result',{}).get('message_id'):raise ValueError()
        return result['result']['message_id']
    except Exception:raise RuntimeError('Telegram delivery not confirmed; secrets omitted') from None


def tick(path=STATE,read=sample,notify=send,clock=None,fallback=None):
    clock=time.time() if clock is None else clock
    candidates=[]
    for candidate in [path,fallback]:
        if candidate is None:continue
        try:candidates.append(json.loads(candidate.read_text(encoding='utf-8')))
        except (OSError,ValueError):pass
    state=max(candidates,key=lambda s:s.get('checked_at',0)) if candidates else {}
    def persist():
        wrote=False
        for candidate in [path,fallback]:
            if candidate is None:continue
            try:save(candidate,state);wrote=True
            except OSError:pass
        if not wrote:print('Resource monitor state unavailable; still attempting critical notification')
    metrics=read();limits=thresholds(metrics);resources=state.setdefault('resources',{});events=[]
    for name,field in [('disk','disk_free'),('memory','memory_available')]:
        item=resources.setdefault(name,{'level':'normal','notified':'normal','last_sent':0})
        current=level(metrics[field],limits[name],item['level']);item['level']=current
        interval=600 if current=='critical' else 1800
        if current!=item['notified'] or (current!='normal' and clock-item['last_sent']>=interval):events.append(name)
    state.update(checked_at=clock,metrics=metrics,thresholds=limits)
    persist()
    if not events:return {'alert':False,'healthy':all(x['level']=='normal' for x in resources.values())}
    warning=any(resources[n]['level']!='normal' for n in events)
    critical=any(resources[n]['level']=='critical' for n in events)
    title='Ива: критически мало ресурсов на сервере' if critical else ('Ива: на сервере мало ресурсов' if warning else 'Ива: ресурсы сервера снова в норме')
    lines=[title,'Сервер 5.63.152.247',datetime.fromtimestamp(clock,timezone(timedelta(hours=5))).strftime('%d.%m.%Y %H:%M (Уфа)')]
    for name in events:
        if name=='disk':lines.append(f"Диск: свободно {metrics['disk_free']/GIB:.2f} ГБ из {metrics['disk_total']/GIB:.1f} ГБ.")
        else:lines.append(f"Доступная ОЗУ: {metrics['memory_available']/MIB:.0f} МБ из {metrics['memory_total']/MIB:.0f} МБ. Свободно swap: {metrics['swap_free']/MIB:.0f} МБ.")
    if warning:lines.append('Нужна проверка нагрузки или места; автоматически данные не удаляю и процессы не останавливаю.')
    try:message_id=notify('\n'.join(lines))
    except Exception:
        state['delivery_error']='Notification not confirmed; retry next check';persist()
        return {'alert':True,'delivered':False}
    for name in events:resources[name].update(notified=resources[name]['level'],last_sent=clock)
    state.pop('delivery_error',None);state['last_message_id']=message_id;persist()
    return {'alert':True,'delivered':True,'message_id':message_id}


if __name__=='__main__':
    result=tick(fallback=RUNTIME);print(json.dumps(result))
