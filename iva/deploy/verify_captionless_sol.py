"""Real Sol replays the reported attachment on an isolated copy, never live."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from iva_integration.finance_agent import run
source='telegram:412850740:608703915:0'
with tempfile.TemporaryDirectory(prefix='captionless-sol-',dir=ROOT/'data') as tmp:
    path=Path(tmp)/'copy.sqlite3'
    src=sqlite3.connect(ROOT/'data/tbank-before-document-recovery.sqlite3')
    dst=sqlite3.connect(path)
    try:
        src.backup(dst)
        dst.execute("UPDATE local_updates SET state='processing' WHERE id=608703915")
        dst.execute('DELETE FROM local_finance_agent_jobs WHERE source=?',(source,))
        dst.commit()
    finally:src.close();dst.close()
    result=run('Сверь текущую справку Т-Банка с реестром и обнови сведения существующего долга по документу. Не создавай новый кредит или расход.',source,db_path=path)
    c=sqlite3.connect(path)
    try:
        row=c.execute("SELECT reported_total_balance,reported_as_of FROM reported_loans WHERE name LIKE '%Банк%' AND reported_total_balance=14850564").fetchone()
        report={'ok':bool(row),'reply':result.get('text'),'steps':[{k:s[k] for k in ['tool','ok']} for s in result.get('steps',[])],
                'model':result.get('model'),'readback':row}
    finally:c.close()
    (ROOT/'data/captionless-sol-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('captionless_sol',report['ok'])
