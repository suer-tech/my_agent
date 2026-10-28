"""Plain user wording through the production Iva engine, isolated chat session."""
import json
from pathlib import Path
import sqlite3
import sys
import time
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.iva_telegram import EveBridge

stamp=str(time.time_ns())
request='Ива, найди мне актуальные подходящие тендеры на разработку или доработку сайтов, приложений, порталов и внедрение ИИ. Учитывай мои возможности и условия участия, которые мы обсуждали. Покажи лучшие варианты со ссылками, сроками и объясни, почему они мне подходят.'
db=sqlite3.connect(ROOT/'data/tender-agent.sqlite3')
before={r[0] for r in db.execute('SELECT source FROM tender_runs')}
db.close()
start=time.time()
report={'request':request,'started_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Production EveBridge and native tools; isolated diagnostic session; no Telegram delivery'}
try:
    report['answer']=EveBridge(ROOT/('data/tender-user-test-'+stamp),skip_daily=True).send(request,'diagnostic:tender-user-test:'+stamp)
    report['completed']=True
    report['answer_chars']=len(report['answer'])
    report['would_be_truncated_in_telegram']=len(report['answer'])>3900
except Exception as error:
    report.update(completed=False,error=str(error))
finally:
    report['elapsed_seconds']=round(time.time()-start,1)
    db=sqlite3.connect(ROOT/'data/tender-agent.sqlite3')
    report['specialist_runs']=[{'source':source,'state':state,'result':json.loads(result) if result else None}
                              for source,state,result in db.execute('SELECT source,state,result FROM tender_runs')
                              if source not in before and source.startswith('diagnostic:')]
    db.close()
    path=ROOT/'data/tender-user-request-verification.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    path.chmod(0o600)
    print('completed',report.get('completed'),'elapsed',report['elapsed_seconds'])
