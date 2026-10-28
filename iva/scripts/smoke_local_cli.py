"""Real CLI + synthetic Telegram transport; never reads .env or contacts Telegram."""
import json
import contextlib
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'examples'))
from demo import populate
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import Bot,OWNER
from local_finance.codex_cli import Codex


class SyntheticTelegram:
    def __init__(self): self.sent=[]
    def send(self,chat,text):
        assert chat==OWNER
        self.sent.append(text)


def update(uid,text,user=OWNER):
    return {'update_id':uid,'message':{'message_id':uid,'chat':{'type':'private','id':user},'from':{'id':user},'text':text}}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as cleanup:
        ledger = Ledger(Path(temp)/'synthetic.sqlite3')
        cleanup.callback(ledger.db.close)
        card,_ = populate(ledger)
        transport = SyntheticTelegram()
        bot = Bot(ledger,Assistant(ledger,Codex()),transport)
        bot.ingest([update(1,'Покажи остатки на всех счетах на конец 2026-10-18. Вызови balances.')])
        bot.process_one()
        assert '57000.00' in transport.sent[-1],transport.sent[-1]
        bot.ingest([update(2,'Предложи запись: расход 100.00 рублей, счёт ДЕМО карта, дата 2026-10-19, назначение синтетическая проверка. Дата верна, это будущий факт; других неоднозначностей нет. Используй stage.')])
        bot.process_one()
        proposal = transport.sent[-1]
        match = re.search(r'/confirm ([a-f0-9]{8})',proposal)
        assert match,proposal
        assert ledger.balances('2026-10-19')[card]==4200000
        bot.ingest([update(3,'/confirm '+match[1])]); bot.process_one()
        assert ledger.balances('2026-10-19')[card]==4190000
        bot.ingest([update(3,'/confirm '+match[1])]); assert not bot.process_one()
        # Recreate assistant: persistent history, not CLI's global last session.
        bot.assistant = Assistant(ledger,Codex())
        bot.ingest([update(4,'Покажи остатки на 2026-10-19 после подтверждённой операции, вызови balances.')]); bot.process_one()
        assert '56900.00' in transport.sent[-1],transport.sent[-1]
        bot.ingest([update(5,'Покажи финансы',user=77)]); assert not bot.process_one()
        report = {'real_codex_cli':True,'telegram_transport':'synthetic; no network/token',
                  'initial_total_rub':'57000.00','final_total_rub':'56900.00',
                  'confirmation_required':True,'duplicate_update_ignored':True,'unauthorized_user_ignored':True,
                  'persistent_context':bool(ledger.rows('SELECT id FROM local_history')),'financial_operations':len(ledger.snapshot()['operations'])}
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
