from pathlib import Path
import json
import sys
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_iva_telegram as fixtures
from local_finance.iva_telegram import IvaTelegramBot,REPLY_KEYBOARD
from local_finance.bot import OWNER,Telegram
from local_finance.voice import Voice,VoiceError,spoken_chunks


class ReplyButtonsTests(unittest.TestCase):
    setUp=fixtures.IvaTelegramTests.setUp
    tearDown=fixtures.IvaTelegramTests.tearDown

    def mode(self):return self.l.rows("SELECT value FROM local_meta WHERE key='voice_mode'")[0]['value']

    def test_buttons_change_real_delivery_and_survive_transport_restart(self):
        self.bot.ingest([fixtures.incoming(1,'🎙 Голосом'),fixtures.incoming(2,'Привет')])
        self.bot.process_one();self.bot.process_one()
        self.assertEqual(self.mode(),'on');self.assertEqual(len(self.api.sent),1);self.assertEqual(len(self.api.voices),1)
        self.assertEqual(len(self.bridge.calls),1)
        self.bot=IvaTelegramBot(self.l,self.assistant,self.api,self.voice,state_dir=self.root/'state')
        self.bot.ingest([fixtures.incoming(3,'Ещё вопрос')]);self.bot.process_one()
        self.assertEqual(len(self.api.voices),2)
        self.bot.ingest([fixtures.incoming(4,'✍️ Текстом'),fixtures.incoming(5,voice={'file_id':'v','duration':3})])
        self.bot.process_one();self.bot.process_one()
        self.assertEqual(self.mode(),'off');self.assertEqual(len(self.api.voices),2)
        self.assertIn('Ответ настоящей Ива',self.api.sent[-1][1])
        self.bot.ingest([fixtures.incoming(4,'✍️ Текстом')]);self.assertFalse(self.bot.process_one())
        self.assertFalse(self.l.rows('SELECT * FROM operations'))

    def test_settings_is_immediate_text_and_does_not_change_mode(self):
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('voice_mode','on')")
        self.bot.ingest([fixtures.incoming(1,'/settings')]);self.bot.process_one()
        self.assertEqual(self.mode(),'on');self.assertIn('голосом',self.api.sent[0][1])
        self.assertFalse(self.bridge.calls);self.assertFalse(self.api.voices)

    def test_buttons_in_caption_foreign_group_or_old_message_cannot_switch(self):
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('voice_mode','off')")
        old=fixtures.incoming(3,'🎙 Голосом');old['message']['date']=50
        group=fixtures.incoming(2,'🎙 Голосом');group['message']['chat']['type']='group'
        self.bot.ingest([fixtures.incoming(1,'🎙 Голосом',user=77),group,old])
        self.assertFalse(self.bot.process_one());self.assertEqual(self.mode(),'off')
        self.api.files['text']=b'hello'
        self.bot.ingest([fixtures.incoming(4,document={'file_id':'text','file_name':'note.txt'},caption='🎙 Голосом')]);self.bot.process_one()
        self.assertEqual(self.mode(),'off')

    def test_voice_failure_keeps_preference_and_delivers_full_text(self):
        self.bot.ingest([fixtures.incoming(1,'🎙 Голосом'),fixtures.incoming(2,'Ответь')]);self.bot.process_one()
        with patch.object(self.voice,'synthesize',side_effect=VoiceError('Синтез временно недоступен')):self.bot.process_one()
        self.assertEqual(self.mode(),'on');self.assertFalse(self.api.voices)
        self.assertIn('Ответ настоящей Ива',self.api.sent[-1][1]);self.assertIn('Синтез временно недоступен',self.api.sent[-1][1])
        self.assertEqual(self.l.rows('SELECT state FROM local_updates WHERE id=2')[0]['state'],'done')

    def test_uncertain_voice_send_does_not_fall_back_or_repeat_task(self):
        self.bot.ingest([fixtures.incoming(1,'🎙 Голосом'),fixtures.incoming(2,'Ответь')]);self.bot.process_one()
        with patch.object(self.api,'send_voice',side_effect=RuntimeError('network')):self.bot.process_one()
        self.assertEqual(len(self.api.sent),1);self.assertEqual(len(self.bridge.calls),1)
        self.assertEqual(self.l.rows('SELECT state FROM local_updates WHERE id=2')[0]['state'],'uncertain')
        self.assertFalse(self.bot.process_one())

    def test_progress_uses_selected_format_and_is_not_replayed(self):
        self.bot.ingest([fixtures.incoming(1,'🎙 Голосом'),fixtures.incoming(2,'Проверь кредит')]);self.bot.process_one()
        with self.l.db:self.l.db.execute("UPDATE local_updates SET state='processing' WHERE id=2")
        event={'source':f'telegram:{OWNER}:2:0','id':'a','message':'Передам финансовому агенту, чтобы сверить условия кредита.'}
        self.bot.deliver_progress(event);self.bot.deliver_progress(event)
        self.assertEqual(len(self.api.voices),1);self.assertEqual(len(self.api.sent),1)
        self.assertEqual(self.voice.spoken,[event['message']])

    def test_keyboard_is_attached_to_text_and_voice_payloads(self):
        api=Telegram('synthetic');api.reply_keyboard=REPLY_KEYBOARD
        with patch.object(api,'call',return_value={}) as call:
            api.send(OWNER,'Настройки');self.assertEqual(call.call_args.args[1]['reply_markup'],REPLY_KEYBOARD)
        with patch.object(api,'request',return_value={}) as request:
            api.send_voice(OWNER,fixtures.OGG)
            self.assertIn(b'name="reply_markup"',request.call_args.args[1])
            self.assertIn('🎙 Голосом'.encode(),request.call_args.args[1])


class CompleteVoiceTests(unittest.TestCase):
    def test_long_reply_reaches_last_number_in_valid_single_opus(self):
        engine=Voice()
        if not engine.ffmpeg:self.skipTest('ffmpeg not installed')
        text='Выполнено финансовым модулем: '+('Сохранила платёж 206 рублей. '*290)+' Последний платёж 177,87 рублей.'
        spoken=[]
        def synth(part,path):
            spoken.append(part)
            with wave.open(str(path),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(44100);wav.writeframes(b'\1\2'*4410)
        with patch.object(engine,'synthesize_wav',side_effect=synth):audio=engine.synthesize(text)
        self.assertGreater(len(spoken),1);self.assertTrue(all(len(p)<=3500 for p in spoken))
        self.assertEqual(' '.join(spoken),text)
        self.assertTrue(audio.startswith(b'OggS'));self.assertIn(b'OpusHead',audio[:512])

    def test_no_silent_truncation_after_currency_expansion(self):
        text=('Платёж 206 RUB. '*400)+'Остаток 116057 RUB.'
        parts=spoken_chunks(text)
        self.assertEqual(' '.join(parts),text.replace('RUB','рублей'))
        self.assertTrue(all(len(p)<=3500 for p in parts))


if __name__=='__main__':unittest.main()
