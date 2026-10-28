from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.bot import Bot,Telegram,OWNER
from local_finance.assistant import Assistant,Ledger
from local_finance.voice import VoiceError,MAX_BYTES

OGG=b'OggS'+b'\x00'*24+b'OpusHead'+b'\x00'*100


class FakeVoice:
    ready=True
    def __init__(self,transcript='Сколько денег?',fail=False):self.transcript=transcript;self.fail=fail;self.spoken=[]
    def transcribe(self,path):
        if self.fail:raise VoiceError('Ошибка распознавания')
        return self.transcript
    def synthesize(self,text):self.spoken.append(text);return OGG


class FakeCLI:
    def __init__(self):self.prompts=[]
    def plan(self,prompt):self.prompts.append(prompt);return 'Тестовый ответ','none',{}


class FakeTelegram:
    def __init__(self):self.texts=[];self.voices=[];self.downloads=0
    def download_voice(self,metadata,path):
        self.downloads+=1;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(OGG);return path
    def send(self,chat,text):self.texts.append((chat,text))
    def send_voice(self,chat,content):self.voices.append((chat,content))


def update(uid,user=OWNER,text=None,audio=False):
    msg={'chat':{'id':user,'type':'private'},'from':{'id':user}}
    if text is None:msg['audio' if audio else 'voice']={'file_id':'synthetic','duration':5,'file_size':100}
    else:msg['text']=text
    return {'update_id':uid,'message':msg}


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.patcher=patch('local_finance.bot.ROOT',self.root);self.patcher.start()
        self.cli=FakeCLI();self.api=FakeTelegram();self.engine=FakeVoice()
        self.bot=Bot(self.l,Assistant(self.l,self.cli),self.api,self.engine)
    def tearDown(self):self.patcher.stop();self.l.db.close();self.temp.cleanup()

    def test_phone_voice_transcript_and_voice_reply_no_duplicate(self):
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('voice_mode','auto')")
        self.bot.ingest([update(1)]);self.bot.process_one()
        self.assertIn('Распознано',self.api.texts[0][1])
        self.assertEqual(len(self.api.voices),1)
        self.assertEqual(self.api.voices[0][0],OWNER)
        self.bot.ingest([update(1)]);self.assertFalse(self.bot.process_one())
        self.assertEqual(self.api.downloads,1)
        self.assertEqual(list((self.root/'data/voice-inbox').glob('*')),[])

    def test_audio_attachment_works_like_voice(self):
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('voice_mode','auto')")
        self.bot.ingest([update(1,audio=True)]);self.bot.process_one()
        self.assertEqual(self.api.downloads,1);self.assertEqual(len(self.api.voices),1)

    def test_unauthorized_voice_never_downloaded(self):
        self.bot.ingest([update(1,user=77)]);self.assertFalse(self.bot.process_one())
        self.assertEqual(self.api.downloads,0);self.assertEqual(self.api.texts,[])

    def test_voice_off_still_transcribes_but_answers_text(self):
        self.bot.ingest([update(1,text='/voice off'),update(2)])
        self.bot.process_one();self.bot.process_one()
        self.assertEqual(len(self.api.texts),2);self.assertEqual(self.api.voices,[])
        self.assertEqual(self.api.downloads,1)

    def test_voice_on_and_oneoff_for_text(self):
        self.bot.ingest([update(1,text='/voice on'),update(2,text='Привет')])
        self.bot.process_one();self.bot.process_one()
        self.assertEqual(len(self.api.voices),1)

    def test_stt_cannot_execute_confirmation_command(self):
        self.engine.transcript='/confirm abc12345'
        self.bot.ingest([update(1)]);self.bot.process_one()
        self.assertEqual(len(self.cli.prompts),1)
        self.assertIn('Непроверенная транскрипция',self.cli.prompts[0])
        self.assertEqual(self.l.snapshot()['operations'],[])

    def test_not_ready_voice_is_queued_without_blocking_text(self):
        self.bot.voice=None
        self.bot.ingest([update(1),update(2,text='Привет')]);self.bot.process_one()
        self.assertEqual(self.api.downloads,0)
        rows=self.l.rows('SELECT id,state FROM local_updates ORDER BY id')
        self.assertEqual(rows,[{'id':1,'state':'pending'},{'id':2,'state':'done'}])
        self.bot.voice=self.engine;self.bot.process_one()
        self.assertEqual(self.api.downloads,1)

    def test_recognition_failure_retains_file_without_financial_changes(self):
        self.engine.fail=True
        self.bot.ingest([update(1)]);self.bot.process_one()
        self.assertTrue((self.root/'data/voice-inbox/1.audio').exists())
        self.assertEqual(self.cli.prompts,[])
        self.assertEqual(self.l.snapshot()['operations'],[])
        self.engine.fail=False
        self.bot.ingest([update(2,text='/retryvoice 1')]);self.bot.process_one();self.bot.process_one()
        self.assertEqual(self.api.downloads,1)
        self.assertEqual(len(self.cli.prompts),1)

    def test_sendvoice_uses_multipart_and_rejects_bad_owner_or_format(self):
        api=Telegram('synthetic-not-a-secret')
        with patch.object(api,'request',return_value={}) as request:
            api.send_voice(OWNER,OGG)
            method,body,ctype=request.call_args.args
            self.assertEqual(method,'sendVoice');self.assertIn(b'name="voice"',body)
            self.assertIn(b'Content-Type: audio/ogg',body);self.assertTrue(ctype.startswith('multipart/'))
            with self.assertRaises(VoiceError):api.send_voice(77,OGG)
            with self.assertRaises(VoiceError):api.send_voice(OWNER,b'RIFF-not-opus')

    def test_download_size_and_path_restricted_before_network(self):
        api=Telegram('synthetic')
        with self.assertRaises(VoiceError):api.download_voice({'file_size':MAX_BYTES+1},self.root/'a')
        with patch.object(api,'call',return_value={'file_path':'../private'}):
            with self.assertRaises(VoiceError):api.download_voice({'file_id':'x'},self.root/'a')


if __name__=='__main__':unittest.main()
