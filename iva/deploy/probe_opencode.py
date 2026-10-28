"""Real free-model checks; no Telegram delivery and no financial writes."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path('/opt/iva');sys.path.insert(0,str(ROOT))


def main():
    p=argparse.ArgumentParser();p.add_argument('--release',required=True,type=Path);a=p.parse_args();release=a.release.resolve()
    sys.path.insert(0,str(release/'source/deploy'))
    from verify_opencode import stage
    stage(release/'source')
    from local_finance.opencode_cli import OpenCode
    from iva_integration.primary_engine import PROMPT,validate
    cli=OpenCode(ROOT)
    cases=[('Как дела? Ответь голосом.','reply',[]),('Переведи на английский: Я буду завтра.','reply',[]),
           ('Запиши расход 206 рублей на фильтры для воды.','workflow',[]),
           ('Найди новые тендеры на разработку сайтов.','workflow',[]),
           ('Посмотри погоду сейчас в Екатеринбурге.','workflow',[]),
           ('Запомни, я уже смотрел фильм Начало.','workflow',[]),
           ('20 октября этого года','workflow',[{'role':'assistant','text':'Какого года первый платёж по кредиту Ozon?'}])]
    report=[]
    for message,route,history in cases:
        started=time.monotonic()
        try:
            value=validate(cli.decide(PROMPT+'\nCURRENT_INPUT='+json.dumps({'date':'2026-10-21','requires_workflow':False,'message':message,'history':history},ensure_ascii=False)))
            report.append({'message':message,'expected':route,'passed':value['route']==route,'seconds':round(time.monotonic()-started,3),'decision':value,'execution':cli.last_execution})
        except Exception as error:report.append({'message':message,'passed':False,'error':str(error),'seconds':round(time.monotonic()-started,3)})
        print(json.dumps(report[-1],ensure_ascii=False),flush=True)
    result={'passed':all(r['passed'] for r in report),'cases':report}
    (release/'routing-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
