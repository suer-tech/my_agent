"""Real STT + Codex CLI + ledger + TTS/Opus. Telegram is entirely synthetic."""
import contextlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import Bot,OWNER
from local_finance.codex_cli import Codex
from local_finance.voice import Voice


class SyntheticTelegram:
    def __init__(self,clips): self.clips=clips;self.texts=[];self.voices=[]
    def download_voice(self,metadata,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(self.clips[metadata['file_id']],path)
        return path
    def send(self,chat,text):
        assert chat==OWNER;self.texts.append(text)
    def send_voice(self,chat,content):
        assert chat==OWNER and content.startswith(b'OggS') and b'OpusHead' in content[:512]
        self.voices.append(content)


def update(uid,voice=None,text=None):
    message={'chat':{'type':'private','id':OWNER},'from':{'id':OWNER}}
    if voice: message['voice']={'file_id':voice,'duration':15,'file_size':50000}
    else: message['text']=text
    return {'update_id':uid,'message':message}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    engine=Voice()
    assert engine.ready,'Run setup_voice.py first'
    with tempfile.TemporaryDirectory() as temp,contextlib.ExitStack() as cleanup:
        root=Path(temp)
        clips={}
        for key,text in [('balances','Скажи, сколько у меня денег на счетах сегодня?'),('expense','Запиши расход сто рублей на продукты сегодня со счета Карта.')]:
            path=root/(key+'.ogg');path.write_bytes(engine.synthesize(text));clips[key]=path
        ledger=Ledger(root/'synthetic.sqlite3');cleanup.callback(ledger.db.close)
        card=ledger.account('Карта','57000.00','2026-10-17')['id']
        ledger.configure('10000.00','500.00',card)
        transport=SyntheticTelegram(clips)
        bot=Bot(ledger,Assistant(ledger,Codex()),transport,engine)
        with patch('local_finance.bot.ROOT',root):
            bot.ingest([update(1,voice='balances')]);bot.process_one()
            assert '57000.00' in transport.texts[-1],transport.texts[-1]
            bot.ingest([update(2,voice='expense')]);bot.process_one()
            proposal=transport.texts[-1]
            match=re.search(r'/confirm ([a-f0-9]{8})',proposal)
            clarification_required = match is None
            if not match:
                # Asking for clarification after STT is expected safe behavior.
                bot.ingest([update(3,text='Да, данные верны: расход 100.00 RUB на продукты, дата 2026-10-18, счёт Карта. Подготовь stage, запись пока не подтверждай.')]);bot.process_one()
                proposal=transport.texts[-1]
                match=re.search(r'/confirm ([a-f0-9]{8})',proposal)
            assert match,proposal
            assert ledger.balances('2026-10-18')[card]==5700000
            bot.ingest([update(4,text='/confirm '+match[1])]);bot.process_one()
            assert ledger.balances('2026-10-18')[card]==5690000
            bot.ingest([update(2,voice='expense')]);assert not bot.process_one()
        transcripts=[json.loads(row['metadata'])['transcript'] for row in ledger.rows("SELECT metadata FROM local_updates WHERE text='/incoming_voice' ORDER BY id")]
        report={'real_local_stt':True,'real_codex_cli':True,'real_windows_tts':True,'real_ogg_opus_conversion':True,
                'telegram_transport':'synthetic only; no Telegram API sends','transcripts_synthetic':transcripts,
                'initial_total_rub':'57000.00','final_total_rub':'56900.00','confirmation_required':True,
                'voice_responses':len(transport.voices),'duplicate_ignored':True,'voice_mode':'auto','clarification_required':clarification_required}
        assert len(transport.voices)==2
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
