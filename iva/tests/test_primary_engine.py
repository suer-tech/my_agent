from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import OWNER
from local_finance.opencode_cli import OpenCode,OpenCodeError,MODEL,read_session_answer
from local_finance.iva_telegram import IvaAssistant,IvaTelegramBot
from iva_integration.primary_engine import PrimaryBridge,mode,validate
import test_iva_telegram as fixtures


def reply(text='На связи, готова помочь!',fmt='keep'):
    return {'route':'reply','scope':'social','reply':text,'ack':'','reply_format':fmt}


def workflow():
    return {'route':'workflow','scope':'needs_tools','reply':'','ack':'Передам финансовому агенту, чтобы проверить кредит.','reply_format':'keep'}


class Fallback:
    def __init__(self):self.calls=[];self.skip_daily=True;self.fail=False;self.reset_count=0
    def send(self,text,source):
        self.calls.append((text,source))
        if self.fail:raise RuntimeError('Backend failed after possible action')
        return 'Проверено рабочим агентом'
    def reset(self):self.reset_count+=1


class Front:
    def __init__(self,value=None,fail=False):self.value=value or reply();self.fail=fail;self.prompts=[];self.last_execution={'model':MODEL,'elapsed_ms':1200}
    def decide(self,prompt):
        self.prompts.append(prompt)
        if self.fail:raise OpenCodeError('Unavailable')
        return dict(self.value)


class PrimaryEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.l=Ledger(':memory:');Assistant(self.l,Mock())
        self.api=fixtures.API();self.front=Front();self.backend=Fallback();self.bridge=PrimaryBridge(self.l,self.backend,self.front,self.root)
        self.assistant=IvaAssistant(self.l,self.bridge,fixtures.CLI())
        self.bot=IvaTelegramBot(self.l,self.assistant,self.api,fixtures.Voice(),state_dir=self.root/'state')
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('primary_engine','opencode')")
        self.patcher=patch('local_finance.bot.ROOT',self.root);self.patcher.start()
    def tearDown(self):self.patcher.stop();self.l.db.close();self.temp.cleanup()
    def accept(self,uid=1,text='Как дела?'):
        self.bot.ingest([fixtures.incoming(uid,text)])
        with self.l.db:self.l.db.execute("UPDATE local_updates SET state='processing' WHERE id=?",(uid,))
        return f'telegram:{OWNER}:{uid}:0'

    def test_simple_voice_answer_bypasses_workflow_and_replay_is_cached(self):
        self.front.value=reply(fmt='voice_once');source=self.accept(text='Как дела? Ответь голосом.')
        answer=self.assistant.handle(OWNER,'Как дела? Ответь голосом.',source)
        self.assertEqual(answer,self.front.value['reply']);self.assertFalse(self.backend.calls)
        metadata=json.loads(self.l.rows('SELECT metadata FROM local_updates WHERE id=1')[0]['metadata']);self.assertTrue(metadata['reply_voice_requested'])
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('primary_engine','codex')")
        self.assertEqual(self.bridge.send('repeat',source),answer);self.assertEqual(len(self.front.prompts),1)
        self.assertEqual(self.bridge.recover_answer(source),answer);self.assertFalse(self.l.rows('SELECT * FROM operations'))

    def test_workflow_ack_precedes_original_task_and_specialist_is_preserved(self):
        self.front.value=workflow();source=self.accept(text='Кредит по скриншотам, перепроверь')
        events=[];self.bridge.on_progress=lambda e:events.append((e,len(self.backend.calls)))
        self.bridge.send_owner('FULL ORIGINAL DOCUMENT AND TASK',source,'Кредит по скриншотам, перепроверь')
        self.assertEqual(events[0][1],0);self.assertIn('финансовому агенту',events[0][0]['message'])
        self.assertIn('FULL ORIGINAL DOCUMENT AND TASK',self.backend.calls[0][0]);self.assertEqual(self.backend.calls[0][1],source)
        self.bridge.send('repeat',source);self.assertEqual(len(self.backend.calls),1)

    def test_codex_selection_bypasses_front_entirely(self):
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('primary_engine','codex')")
        self.front.fail=True;self.bridge.send('task','source')
        self.assertFalse(self.front.prompts);self.assertEqual(self.backend.calls,[('task','source')])

    def test_failure_falls_back_once_and_circuit_avoids_next_timeout(self):
        self.front.fail=True
        self.bridge.send('first',self.accept(1));self.bridge.send('second',self.accept(2))
        self.assertEqual(len(self.front.prompts),1);self.assertEqual(len(self.backend.calls),2);self.assertEqual(mode(self.l),'opencode')

    def test_backend_failure_never_replays_actions(self):
        self.front.value=workflow();self.backend.fail=True;source=self.accept()
        with self.assertRaises(RuntimeError):self.bridge.send('task',source)
        with self.assertRaises(RuntimeError):self.bridge.send('task',source)
        self.assertEqual(len(self.backend.calls),1)

    def test_document_cannot_be_answered_from_truncated_front_context(self):
        incoming=fixtures.incoming(1,document={'file_id':'x','file_name':'file.pdf'})
        self.bot.ingest([incoming])
        with self.l.db:self.l.db.execute("UPDATE local_updates SET state='processing' WHERE id=1")
        self.bridge.send_owner('COMPLETE ATTACHMENT',f'telegram:{OWNER}:1:0','Document description')
        self.assertIn('COMPLETE ATTACHMENT',self.backend.calls[0][0]);self.assertIn('"requires_workflow": true',self.front.prompts[0])

    def test_long_input_reaches_full_existing_workflow(self):
        text='а'*12000+' ВАЖНЫЙ ХВОСТ';source=self.accept(text=text)
        self.bridge.send_owner(text,source,text)
        self.assertIn('ВАЖНЫЙ ХВОСТ',self.backend.calls[0][0])

    def test_settings_switch_without_model_even_when_front_is_broken(self):
        self.front.fail=True
        self.bot.ingest([fixtures.incoming(1,'🧠 Codex / Luna')]);self.bot.process_one()
        self.assertEqual(mode(self.l),'codex');self.assertFalse(self.front.prompts);self.assertFalse(self.backend.calls)
        with patch('iva_integration.primary_engine.available',return_value=True):
            self.bot.ingest([fixtures.incoming(2,'⚡ OpenCode')]);self.bot.process_one()
        self.assertEqual(mode(self.l),'opencode');self.assertFalse(self.front.prompts)
        self.bot.ingest([fixtures.incoming(3,'⚙️ Настройки')]);self.bot.process_one()
        self.assertIn('Sol 6.1',self.api.sent[-1][1]);self.assertFalse(self.api.voices)

    def test_foreign_or_document_source_cannot_switch_engine(self):
        self.bot.ingest([fixtures.incoming(1,'🧠 Codex / Luna',user=77)]);self.assertFalse(self.bot.process_one())
        with self.assertRaises(PermissionError):self.assistant.handle(OWNER,'/engine codex','document:untrusted')
        self.assertEqual(mode(self.l),'opencode')

    def test_unavailable_opencode_does_not_replace_working_selection(self):
        with self.l.db:self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('primary_engine','codex')")
        self.bot.ingest([fixtures.incoming(1,'⚡ OpenCode')])
        with patch('iva_integration.primary_engine.available',return_value=False):self.bot.process_one()
        self.assertEqual(mode(self.l),'codex');self.assertIn('недоступен',self.api.sent[-1][1])

    def test_reset_limits_front_history_but_preserves_existing_records(self):
        with self.l.db:self.l.db.execute('INSERT INTO local_history(chat,role,text) VALUES(?,?,?)',(OWNER,'user','OLD SECRET CONTEXT'))
        self.bridge.reset();source=self.accept();self.assistant.handle(OWNER,'Как дела?',source)
        self.assertNotIn('OLD SECRET CONTEXT',self.front.prompts[0]);self.assertEqual(self.backend.reset_count,1)
        self.assertTrue(self.l.rows("SELECT id FROM local_history WHERE text='OLD SECRET CONTEXT'"))

    def test_daily_memory_records_both_sides_once(self):
        self.backend.skip_daily=False;source=self.accept();self.bridge.send_owner('context',source,'Как дела?');self.bridge.send('repeat',source)
        text=next((self.root/'data/iva-pilot/app/vault/daily').glob('*.md')).read_text(encoding='utf-8')
        self.assertEqual(text.count('primary-turn:'),1);self.assertIn('Как дела?',text);self.assertIn(self.front.value['reply'],text)


class OpenCodeCliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'data').mkdir()
        exe=self.root/'cli';exe.touch()
        self.cfg={'enabled':True,'model':MODEL,'executable':str(exe),'state_root':str(self.root/'isolated'),'timeout_seconds':12}
        (self.root/'data/opencode-primary.json').write_text(json.dumps(self.cfg))
    def tearDown(self):self.temp.cleanup()

    def test_pinned_free_model_and_no_inherited_credentials_or_tools(self):
        process=Mock(returncode=0);process.communicate.return_value=(json.dumps({'type':'text','part':{'type':'text','text':json.dumps(reply())}}).encode(),b'')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'synthetic','CODEX_HOME':'private-secret','TELEGRAM_BOT_TOKEN':'synthetic'}),patch('local_finance.opencode_cli.subprocess.Popen',return_value=process) as popen:
            self.assertEqual(OpenCode(self.root).decide('prompt'),reply())
            args=popen.call_args.args[0];env=popen.call_args.kwargs['env']
            self.assertEqual(args[args.index('--model')+1],MODEL)
            self.assertFalse(set(env)&{'OPENAI_API_KEY','CODEX_HOME','TELEGRAM_BOT_TOKEN'})
            cfg=json.loads(env['OPENCODE_CONFIG_CONTENT']);self.assertEqual(cfg['permission'],{'*':'ask'});self.assertEqual(cfg['small_model'],MODEL)
            self.assertNotIn('--auto',args);self.assertEqual(cfg['agent']['build']['permission'],{'*':'ask'})

    def test_tool_attempt_and_error_event_are_rejected(self):
        for event in [{'type':'tool_use','part':{'type':'tool'}},{'type':'error','error':{'message':'private'}}]:
            process=Mock(returncode=0);process.communicate.return_value=(json.dumps(event).encode(),b'')
            with patch('local_finance.opencode_cli.subprocess.Popen',return_value=process):
                with self.assertRaises(OpenCodeError):OpenCode(self.root).decide('prompt')

    def test_malformed_routing_cannot_claim_an_action(self):
        for value in [reply()|{'scope':'needs_tools'},workflow()|{'reply':'Сохранила расход'},reply()|{'extra':'tool'},reply()|{'reply_format':'invalid'}]:
            with self.assertRaises(OpenCodeError):validate(value)

    def test_attached_cli_reads_only_its_completed_free_model_result(self):
        password=self.root/'password';password.write_text('synthetic')
        cfg={**self.cfg,'server_url':'http://127.0.0.1:18726','password_file':str(password)}
        class Response:
            def __init__(self,data):self.data=json.dumps(data).encode()
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return self.data
        messages=[{'info':{'role':'assistant','modelID':'deepseek-v4.1-flash-free','providerID':'opencode','time':{'completed':123},'finish':'stop','tokens':{'reasoning':0}},'parts':[{'type':'text','text':json.dumps(reply())}]}]
        with patch('local_finance.opencode_cli.urllib.request.urlopen',side_effect=[Response([{'id':'own','title':'unique'},{'id':'other','title':'other'}]),Response(messages)]) as network:
            text,usage=read_session_answer(cfg,'unique')
            self.assertEqual(json.loads(text),reply());self.assertEqual(usage['actual_model'],MODEL)
            self.assertTrue(network.call_args.args[0].full_url.endswith('/session/own/message'))
            self.assertTrue(all(call.args[0].get_method()=='GET' for call in network.call_args_list))
        messages[0]['parts'].append({'type':'tool','state':{'status':'error'}})
        with patch('local_finance.opencode_cli.urllib.request.urlopen',side_effect=[Response([{'id':'own','title':'unique'}]),Response(messages)]):
            with self.assertRaises(OpenCodeError):read_session_answer(cfg,'unique')

    def test_timeout_cancels_native_session_before_fallback(self):
        process=Mock(returncode=-1,pid=123);process.communicate.side_effect=[subprocess.TimeoutExpired('opencode',12),(b'',b'')]
        with patch('local_finance.opencode_cli.subprocess.Popen',return_value=process),patch('local_finance.opencode_cli.os.killpg',create=True),patch('local_finance.opencode_cli.cancel_session') as cancel:
            with self.assertRaises(OpenCodeError):OpenCode(self.root).decide('prompt')
            cancel.assert_called_once();self.assertTrue(cancel.call_args.args[1].startswith('Iva primary '))


if __name__=='__main__':unittest.main()
