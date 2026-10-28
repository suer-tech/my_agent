from datetime import datetime,timedelta
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from local_finance.assistant import Ledger
from local_finance.bot import OWNER,Telegram
from local_finance.iva_telegram import EveBridge,IvaAssistant,IvaTelegramBot,ZONE
from test_iva_telegram import Bridge,CLI,API,Voice,incoming


class ProgressTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.root_patch=patch('local_finance.bot.ROOT',self.root);self.root_patch.start()
        self.l=Ledger(':memory:');self.bridge=Bridge();self.api=API()
        self.assistant=IvaAssistant(self.l,self.bridge,CLI())
        self.bot=IvaTelegramBot(self.l,self.assistant,self.api,Voice(),state_dir=self.root/'state')
        self.source=f'telegram:{OWNER}:1:0'

    def tearDown(self):
        self.root_patch.stop();self.l.db.close();self.tmp.cleanup()

    def event(self,event_id='one',message='Сверю бюджет с финансовым агентом.',source=None):
        return {'kind':'progress','source':source or self.source,'id':event_id,'message':message}

    def processing(self):
        self.bot.ingest([incoming(1,'Проверь бюджет')])
        with self.l.db:self.l.db.execute("UPDATE local_updates SET state='processing' WHERE id=1")

    def test_meaningful_update_arrives_before_final_answer_and_work_runs_once(self):
        original=self.bridge.send
        def send(text,source):
            self.bridge.on_progress(self.event())
            self.assertEqual(self.api.sent[0][1],'Сверю бюджет с финансовым агентом.')
            return original(text,source)
        self.bridge.send=send
        self.bot.ingest([incoming(1,'Проверь бюджет')]);self.bot.process_one()
        self.assertEqual(len(self.api.sent),2)
        self.assertIn('Ответ настоящей Ива',self.api.sent[1][1])
        self.bot.ingest([incoming(1,'Проверь бюджет')]);self.assertFalse(self.bot.process_one())
        self.assertEqual(len(self.bridge.calls),1);self.assertEqual(len(self.api.sent),2)

    def test_progress_send_failure_preserves_final_and_is_not_retried(self):
        original_send=self.api.send;original_bridge=self.bridge.send;attempts=[]
        def flaky(chat,text):
            attempts.append(text)
            if len(attempts)==1:raise RuntimeError('lost response after send')
            return original_send(chat,text)
        self.api.send=flaky
        def send(text,source):
            self.bridge.on_progress(self.event());self.bridge.on_progress(self.event())
            return original_bridge(text,source)
        self.bridge.send=send
        self.bot.ingest([incoming(1,'Проверь бюджет')]);self.bot.process_one()
        self.assertEqual(len(attempts),2);self.assertEqual(len(self.bridge.calls),1)
        self.assertEqual(self.l.rows('SELECT state FROM local_iva_progress')[0]['state'],'uncertain')
        self.assertEqual(self.l.rows('SELECT state FROM local_updates')[0]['state'],'done')

    def test_wrong_owner_historical_and_unbound_progress_cannot_send(self):
        self.bot.deliver_progress(self.event());self.assertFalse(self.api.sent)
        self.processing()
        self.bot.deliver_progress(self.event(source='telegram:123:1:0'))
        self.bot.deliver_progress(self.event(source=f'telegram:{OWNER}:2:0'))
        with self.l.db:self.l.db.execute("UPDATE local_updates SET state='done' WHERE id=1")
        self.bot.deliver_progress(self.event());self.assertFalse(self.api.sent)

    def test_duplicate_updates_throttle_and_total_limit(self):
        self.processing();self.bot.deliver_progress(self.event())
        self.bot.deliver_progress(self.event('two','Теперь проверю детали.'))
        self.assertEqual(len(self.api.sent),1)
        for n in range(2,7):
            with self.l.db:self.l.db.execute('UPDATE local_iva_progress SET at=?',((datetime.now(ZONE)-timedelta(seconds=20)).isoformat(),))
            self.bot.deliver_progress(self.event(str(n),f'Проверю этап {n}.'))
        self.assertEqual(len(self.api.sent),4)

    def test_reconstructed_bot_does_not_resend_uncertain_update(self):
        self.processing()
        with self.l.db:self.l.db.execute("INSERT INTO local_iva_progress VALUES(?,?,?,'sending',?,NULL)",(self.source,'one','claimed before crash',(datetime.now(ZONE)-timedelta(seconds=20)).isoformat()))
        self.bot=IvaTelegramBot(self.l,self.assistant,self.api,Voice(),state_dir=self.root/'state')
        self.bot.deliver_progress(self.event());self.assertFalse(self.api.sent)

    def test_bridge_reads_incremental_stdout_before_process_exits(self):
        marker=self.root/'delivered'
        event=self.event()
        # Child refuses to finish until the progress callback has run.
        script="import json,sys,time,pathlib; request=json.load(sys.stdin); print(json.dumps("+repr(event)+"),flush=True); p=pathlib.Path(sys.argv[1]); end=time.monotonic()+4\nwhile not p.exists() and time.monotonic()<end: time.sleep(.02)\nassert p.exists(), 'progress was buffered'; print(json.dumps({'source':request['source'],'status':'done','message':'final'}),flush=True)"
        process=subprocess.Popen([sys.executable,'-c',script,str(marker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        bridge=EveBridge(state_dir=self.root/'diagnostic',skip_daily=True)
        bridge.on_progress=lambda value:marker.write_text(value['message'])
        result=bridge.read_turn(process,{'source':self.source},timeout=6)
        self.assertEqual(process.returncode,0);self.assertEqual(result['message'],'final')
        self.assertEqual(marker.read_text(),'Сверю бюджет с финансовым агентом.')

    def test_bridge_callback_failure_does_not_hide_final_or_replay(self):
        event=self.event()
        script="import json,sys; request=json.load(sys.stdin); print(json.dumps("+repr(event)+"),flush=True); print(json.dumps({'source':request['source'],'status':'done','message':'final'}),flush=True)"
        process=subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        bridge=EveBridge(state_dir=self.root/'diagnostic',skip_daily=True)
        def fail(value):raise RuntimeError('offline')
        bridge.on_progress=fail
        self.assertEqual(bridge.read_turn(process,{'source':self.source},timeout=5)['message'],'final')

    def test_auxiliary_requests_have_short_timeouts_and_keep_proxy(self):
        telegram=Telegram('fake-token')
        with patch.object(telegram,'open',side_effect=lambda *args,**kwargs:io.BytesIO(b'{"ok":true,"result":{}}')) as opened:
            telegram.typing(OWNER);telegram.send_progress(OWNER,'Сейчас проверю.')
        self.assertEqual([call.kwargs['timeout'] for call in opened.call_args_list],[2,4])


if __name__=='__main__':unittest.main()
