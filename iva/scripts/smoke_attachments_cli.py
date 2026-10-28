"""Real Codex image/PDF reading; synthetic files/DB, no Telegram network."""
import contextlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import OWNER
from local_finance.attachments import Attachments,decode_text
from local_finance.codex_cli import Codex


class SyntheticAPI:
    def download_attachment(self,metadata,path):
        path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/'data/attachment-fixtures'/metadata['file_id'],path);return path


def confirm(assistant,text):
    match=re.search(r'/confirm ([a-f0-9]{8})',text)
    assert match,text
    return assistant.handle(OWNER,'/confirm '+match[1],'synthetic:confirm:'+match[1])


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with tempfile.TemporaryDirectory() as temp,contextlib.ExitStack() as cleanup:
        root=Path(temp);l=Ledger(root/'synthetic.sqlite3');cleanup.callback(l.db.close)
        account=l.account('Карта','10000.00','2026-10-17')['id']
        l.schedule('Альфа','expense','5900.00',account,monthday=20,start='2026-10-19')
        a=Assistant(l,Codex(timeout=180));reader=Attachments(l,a,SyntheticAPI())
        with patch('local_finance.attachments.ROOT',root):
            receipt={'kind':'image','file_id':'receipt.png','caption':'Предложи расход по чеку со счёта Карта. Все суммы и дата видны, запиши только после подтверждения.'}
            answer=reader.handle({'id':1,'chat':OWNER,'text':'/incoming_image'},receipt)
            assert l.snapshot()['operations']==[]
            confirm(a,answer)
            assert l.balances('2026-10-18')[account]==970000
            repeat=reader.handle({'id':2,'chat':OWNER,'text':'/incoming_image'},dict(receipt))
            assert 'уже учтён' in repeat
            pdf={'kind':'pdf','file_id':'bank-schedule.pdf','caption':'Точный банковский график кредита Альфа','loan_hint':'Альфа'}
            answer=reader.handle({'id':3,'chat':OWNER,'text':'/incoming_pdf'},pdf)
            assert not l.snapshot()['bank_schedules']
            confirm(a,answer)
            assert len(l.rows('SELECT id FROM bank_payments'))==3
            f=l.forecast('2026-10-18','2026-10-31')
            assert f['days'][-1]['total']==379988,f['days'][-1]['total']
            repeat=reader.handle({'id':4,'chat':OWNER,'text':'/incoming_pdf'},dict(pdf))
            assert 'уже учтён' in repeat
            assert len(l.rows('SELECT id FROM bank_payments'))==3
        dates=[r['on_date'] for r in l.rows('SELECT on_date FROM bank_payments ORDER BY on_date')]
        assert dates==['2026-10-20','2026-11-23','2026-12-21']
        for encoding in ('utf-8','utf-16','cp1251'):assert 'Планируемые' in decode_text((ROOT/'data/attachment-fixtures'/f'list-{encoding}.txt').read_bytes())
        print(json.dumps({'real_codex_image_input':True,'real_receipt_reading':True,'receipt_total_rub':'300.00','cash_tendered_not_mistaken_for_total':True,
                          'pdf_pages':2,'pdf_second_page_scanned':True,'exact_payments':3,'bank_dates_preserved':True,
                          'forecast_uses_bank_not_approximate_rule':True,'duplicate_receipt_and_pdf_ignored':True,
                          'text_encodings':['utf-8','utf-16','cp1251'],'telegram_transport':'synthetic only'},indent=2))


if __name__=='__main__':main()
