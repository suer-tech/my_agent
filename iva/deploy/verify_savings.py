"""Real finance model; synthetic goals in a temporary DB, never the live ledger."""
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger,Assistant
from iva_integration.finance_agent import run
from iva_integration.run_pilot import env
os.environ.update(env())

with tempfile.TemporaryDirectory(prefix='savings-check-') as tmp:
    db=Path(tmp)/'isolated.sqlite3'
    now=datetime.now(timezone(timedelta(hours=5))).date()
    first=now+timedelta(days=1);end=now+timedelta(days=75)
    l=Ledger(db);Assistant(l,None)
    l.account('Общий','50000',now.isoformat());l.db.close()
    body=f'Посчитай план накоплений «Тест подарков»: жене книга 1000 рублей, старшей дочке игра 2000 рублей, младшей конструктор 3000 рублей. Купить до {end}. Первый взнос {first}, далее каждого {first.day} числа. Пока только расчёт, не включай в бюджет. Неизвестные доходы и резерв отметь как ограничения, не уточняй сейчас.'
    result=run(body,'verification:savings:preview',db_path=db,body_override=body)
    l=Ledger(db)
    proposals=l.savings_list()['proposals'];before=len(l.savings_list()['goals']);l.db.close()
    if not proposals or before:raise RuntimeError('Preview missing or prematurely activated')
    approval='Да, включи рассчитанный план «Тест подарков» в бюджет с предложенными взносами и напоминаниями. Расчёт сохранён в savings_list proposals; суммы там в копейках. Сохрани именно этот план.'
    result2=run(approval,'verification:savings:agree',db_path=db,body_override=approval)
    l=Ledger(db);goals=l.savings_list()['goals']
    report={'preview_ok':result.get('ok'),'no_active_goal_before_agreement':before==0,
            'agreed':result2.get('ok'),'goals':len(goals),
            'target':goals[0]['plan']['target'] if goals else None,
            'cash_postings':len(l.rows('SELECT * FROM operations')),
            'pending_codes':len(l.rows('SELECT * FROM local_pending')),
            'reply':result2.get('text')}
    l.db.close()
    (ROOT/'data/savings-verification.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    if not (report['agreed'] and report['goals']==1 and report['target']==600000 and report['cash_postings']==0 and report['pending_codes']==0):
        raise RuntimeError('Savings integration verification failed')
