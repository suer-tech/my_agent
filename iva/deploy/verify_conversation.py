"""Read-only real-model checks; capture progress without sending Telegram messages."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
import uuid
import urllib.request
import argparse

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.iva_telegram import EveBridge,CONVERSATION_GUIDANCE


def main():
    release=ROOT/'data/conversation-release-20261021'
    parser=argparse.ArgumentParser();parser.add_argument('--case');args=parser.parse_args()
    cfg=json.loads((ROOT/'data/iva-pilot/control.json').read_text())
    for attempt in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{cfg['iva_port']}/",timeout=2) as response:
                if response.status==200:break
        except OSError:
            if attempt==59:raise
            time.sleep(1)
    cases=[
        ('finance','Проверь по финансовому реестру общий остаток и ближайшие обязательные платежи. Ничего не меняй.'),
        ('tenders','Покажи текущий статус последнего исследования тендеров: сколько обработано и что осталось. Новое исследование не запускай.'),
        ('web','Посмотри в интернете страницу https://example.com и скажи, какой у неё заголовок и для чего она предназначена.'),
        ('thinking','Помоги продумать, как организовать личные заметки, чтобы не терять идеи и быстро находить нужное. Ничего не сохраняй и не меняй.'),
        ('instant','Привет, Ива!'),
    ]
    report_path=release/'native-verification.json'
    reports=json.loads(report_path.read_text())['cases'] if args.case and report_path.exists() else []
    for name,text in cases:
        if args.case and args.case!=name:continue
        run_id=uuid.uuid4().hex[:12]
        bridge=EveBridge(state_dir=release/'checks'/f'{name}-{run_id}',skip_daily=True)
        started=time.monotonic();updates=[]
        def progress(value):
            record={**value,'seconds':round(time.monotonic()-started,3)};updates.append(record)
            print(json.dumps({'case':name,'progress':record},ensure_ascii=False),flush=True)
        bridge.on_progress=progress
        try:
            answer=bridge.send(CONVERSATION_GUIDANCE+'\n\nДиагностический запрос: только чтение, без записи новых фактов или изменения реестра. Следуй обычной политике общения с владельцем.\n\n'+text,'diagnostic:conversation:'+name+':'+run_id)
            report={'case':name,'ok':True,'request':text,'updates':updates,'progress_ok':bool(updates) if name!='instant' else not updates,'final':answer,'seconds':round(time.monotonic()-started,3)}
        except Exception as error:
            report={'case':name,'ok':False,'request':text,'updates':updates,'error_type':type(error).__name__,'seconds':round(time.monotonic()-started,3)}
        reports=[old for old in reports if old['case']!=name]+[report]
        (release/'native-verification.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'cases':reports},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
