"""Real read-only native turn and authorized owner-only text/voice delivery."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from local_finance.iva_telegram import EveBridge
from local_finance.bot import Telegram,load_env,OWNER
from local_finance.voice import Voice
report={}
path=ROOT/'data/native-live-verification.json'
def save():
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    path.chmod(0o600)

try:
    config=json.loads((ROOT/'data/iva-pilot/control.json').read_text())
    for key,route in [('gateway_port','/health'),('iva_port','/')]:
        deadline=time.monotonic()+90
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{config[key]}{route}",timeout=3) as r:
                    if r.status==200:break
            except OSError:pass
            if time.monotonic()>deadline:raise RuntimeError('Readiness timeout')
            time.sleep(1)
    report['ready']=True;save()
    bridge=EveBridge(ROOT/'data/native-live-verification-session',skip_daily=True)
    answer=bridge.send('Это проверка после переключения подключения. Вызови finance_agent: только прочитай текущий финансовый реестр и сообщи, доступны ли сведения по кредитам и доходам. Ничего не сохраняй и не изменяй. Ответь кратко по-русски.',
                       'diagnostic:native-live-'+str(time.time_ns()))
    report['native_finance_answer']=answer;save()
    load_env(ROOT/'.env')
    api=Telegram(os.environ['TELEGRAM_BOT_TOKEN'])
    report['telegram_authorized']=bool(api.call('getMe',{}).get('id'));save()
    result=api.send(OWNER,'Проверка после переключения Ивы на прямое подключение через подписку ChatGPT.\n\n'+answer)
    report['text_message_id']=result['message_id'];save()
    audio=Voice().synthesize('Привет! Это Ива. Проверяю голос после переключения подключения. Если ты слышишь это сообщение, голосовой ответ доставлен.')
    result=api.send_voice(OWNER,audio)
    report['voice_message_id']=result['message_id']
    report['voice_bytes']=len(audio)
    report['complete']=True;save()
    print('live_native_text_voice_check_complete')
except Exception as exc:
    report['error_type']=type(exc).__name__;save()
    print('verification_failed:'+type(exc).__name__)
    raise SystemExit(1)
