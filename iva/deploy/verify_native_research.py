"""Real Iva reads the existing job; does not start research or send Telegram."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from local_finance.iva_telegram import EveBridge

def count():
    with sqlite3.connect(f'file:{ROOT}/data/tender-agent.sqlite3?mode=ro',uri=True) as c:
        return c.execute('SELECT count(*) FROM research_jobs').fetchone()[0]

before=count()
answer=EveBridge(ROOT/'data/research-release-20261021/native-status',skip_daily=True).send(
    'Диагностическая проверка нового инструмента. Вызови tender_agent с action=status, '
    'job_id=d49cc283a973 и task="Проверка текущего прогресса". Сообщи ID, этап, '
    'число завершённых запросов, страниц, уникальных закупок и доступность обработчика. '
    'Ничего не запускай, не возобновляй, не отменяй и не меняй в памяти. '
    'Не используй action=research/quick. Это только чтение уже существующей задачи.',
    'diagnostic:native-research-status:'+str(time.time_ns()))
assert count()==before,'Status must not create new research jobs'
assert 'd49cc283a973' in answer,'Iva did not read back the requested job'
(ROOT/'data/research-release-20261021/native-status-verification.json').write_text(
    json.dumps({'answer':answer,'jobs_unchanged':True},ensure_ascii=False,indent=2))
print(answer)
