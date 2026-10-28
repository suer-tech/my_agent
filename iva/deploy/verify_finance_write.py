"""Exercise the real Sol write workflow in a temporary isolated ledger only."""
import json
import os
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger,Assistant
from iva_integration.finance_agent import run
from iva_integration.run_pilot import env
os.environ.update(env())

with tempfile.TemporaryDirectory(prefix='finance-write-check-') as tmp:
    db=Path(tmp)/'isolated.sqlite3'
    l=Ledger(db);Assistant(l,None);l.db.close()
    body='Сохрани сведения о новой тестовой кредитной карте «Проверка». Долг 10000 рублей, обязательный платёж 1000 рублей каждый месяц, дата пока неизвестна. Ставка 20 процентов годовых. Процентная часть платежа и условия досрочного погашения неизвестны. Это сведения для будущей стратегии, не расход.'
    result=run(body,'verification:isolated-card-write',db_path=db,body_override=body)
    l=Ledger(db)
    cards=l.rows('SELECT * FROM credit_cards')
    report={'ok':result.get('ok'),'applied':any(s['tool']=='finance_apply' and s['ok'] for s in result.get('steps',[])),'card_count':len(cards),'cash_postings':len(l.rows('SELECT * FROM operations')),'pending_codes':len(l.rows('SELECT * FROM local_pending')),'result':result.get('text')}
    report['retry_cached']=run(body,'verification:isolated-card-write',db_path=db,body_override=body).get('cached',False)
    l.db.close()
    (ROOT/'data/finance-write-verification.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    if not (report['applied'] and report['card_count']==1 and report['cash_postings']==0 and report['pending_codes']==0 and report['retry_cached']):raise RuntimeError('Isolated finance write verification failed')
