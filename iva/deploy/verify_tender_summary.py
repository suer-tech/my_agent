"""Replay the already fetched specialist evidence through the real Luna formatter."""
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from iva_integration.gateway import model_answer

previous=json.loads((ROOT/'data/tender-user-request-verification.json').read_text())
specialist=previous['specialist_runs'][0]['result']
messages=[
    {'role':'user','content':previous['request']},
    {'role':'assistant','content':None,'tool_calls':[{'id':'verified-search','type':'function','function':{'name':'tender_agent','arguments':json.dumps({'task':previous['request']},ensure_ascii=False)}}]},
    {'role':'tool','tool_call_id':'verified-search','content':json.dumps(specialist,ensure_ascii=False)}
]
text,calls,usage,execution=model_answer({'messages':messages,'tools':[]})
report={'answer':text,'answer_chars':len(text),'tool_calls':len(calls),'execution':execution,
        'scope':'Real Luna replay of the actual specialist response; no new EIS queries'}
(ROOT/'data/tender-summary-verification.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
if len(text)>3900 or calls:raise RuntimeError('Telegram answer budget exceeded')
print('summary_check_complete')
