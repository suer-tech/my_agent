"""Real Iva -> Sol readback, in a separate session with no write authorization."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.iva_telegram import EveBridge

answer=EveBridge(ROOT/'data/credit-readback-check',skip_daily=True).send('Через finance_agent прочитай актуальные сохранённые сведения по кредитной карте Сбер и Т-Банку. Проверь, видны ли сведения из документов 19 октября: ставки и их условия, оставшиеся платежи Сбера, состав задолженности Т-Банка. Ничего не записывай и не изменяй. Кратко сообщи найденные данные.','diagnostic:credit-readback-2026-10-20-v1')
path=ROOT/'data/credit-readback-verification.json'
path.write_text(json.dumps({'answer':answer},ensure_ascii=False),encoding='utf-8')
path.chmod(0o600)
print('credit_readback_complete')
