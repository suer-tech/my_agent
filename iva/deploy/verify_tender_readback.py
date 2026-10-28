"""Native Iva -> tender specialist, read-only profile check."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.iva_telegram import EveBridge

answer=EveBridge(ROOT/'data/tender-readback-final',skip_daily=True).send(
    'Вызови tender_agent: только прочитай текущий профиль поиска тендеров владельца. '
    'Какая форма участия, география, размер закупок, обеспечение заявки и режим поиска? '
    'Объясни, уменьшится ли обеспечение заявки по 44-ФЗ при снижении предлагаемой цены. '
    'Это диагностическая проверка, не ищи закупки, ничего не записывай и не меняй.',
    'diagnostic:tender-readback-2026-10-20-final')
path=ROOT/'data/tender-readback-verification.json'
path.write_text(json.dumps({'answer':answer},ensure_ascii=False),encoding='utf-8')
path.chmod(0o600)
print('tender_readback_complete')
