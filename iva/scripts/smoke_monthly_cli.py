"""Real Codex CLI monthly intake on synthetic data, no Telegram calls."""
import contextlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import OWNER
from local_finance.codex_cli import Codex
from local_finance import intake


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with tempfile.TemporaryDirectory() as temp,contextlib.ExitStack() as cleanup:
        ledger=Ledger(Path(temp)/'synthetic.sqlite3');cleanup.callback(ledger.db.close)
        account=ledger.account('Карта','1000.00','2026-10-18')['id']
        a=Assistant(ledger,Codex())
        first=a.handle(OWNER,'Планируемые ежемесячные траты. Категория Связь: отдельные строки Интернет 100.00 RUB и Телефон 200.00 RUB. Указанный итог категории 400.00 RUB. Даты ещё не указаны. Фактических трат не было. Сохрани весь список через intake со строками и итогом раздельно.','synthetic:list')
        assert intake.context(ledger,OWNER).get('draft'),first
        assert ledger.snapshot()['operations']==[]
        second=a.handle(OWNER,'Считать отдельные строки, итоги не прибавлять. Интернет — 20 числа, телефон — 22 числа каждого месяца. Начало 2026-10-19, счёт Карта. Подготовь общее batch-предложение двух правил для подтверждения.','synthetic:dates')
        match=re.search(r'/confirm ([a-f0-9]{8})',second)
        assert match,second
        assert ledger.snapshot()['rules']==[]
        confirmed=a.handle(OWNER,'/confirm '+match[1],'synthetic:confirm')
        assert len(ledger.snapshot()['rules'])==2,confirmed
        assert ledger.snapshot()['operations']==[]
        forecast=ledger.forecast('2026-10-18','2026-10-31')
        assert forecast['days'][-1]['total']==70000
        a.handle(OWNER,'/confirm '+match[1],'synthetic:repeat')
        assert len(ledger.snapshot()['rules'])==2
        print(json.dumps({'real_codex_cli':True,'telegram':'not used','intake_persistent':True,'details_selected_by_user':True,'category_total_not_added':True,'confirmed_rules':2,'actual_expenses_created':0,'opening_balance_rub':'1000.00','forecast_end_rub':'700.00','lead_days':[r['lead_days'] for r in ledger.snapshot()['rules']],'repeat_confirmation_ignored':True},indent=2))


if __name__=='__main__':main()
