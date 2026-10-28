import json
from pathlib import Path
import re
import sys
import unittest
import tempfile
import hashlib
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
import subprocess

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import Bot,OWNER
from local_finance.codex_cli import CliError,Codex,sync_refreshed_login


class FakeCLI:
    def __init__(self,*answers): self.answers=list(answers);self.prompts=[]
    def plan(self,prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer,Exception): raise answer
        return answer


class FakeTelegram:
    def __init__(self,fail=False): self.sent=[];self.fail=fail
    def send(self,chat,text):
        if self.fail: raise RuntimeError('network')
        self.sent.append((chat,text))


def update(uid,text,user=OWNER,group=False):
    return {'update_id':uid,'message':{'chat':{'id':user,'type':'group' if group else 'private'},'from':{'id':user},'text':text}}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.l = Ledger(':memory:')
        self.a = self.l.account('Карта','1000.00','2026-10-01')['id']
    def tearDown(self): self.l.db.close()
    def expense(self): return {'data':{'kind':'expense','amount':'100.00','date':'2026-10-02','account':self.a,'ambiguities':[]}}
    def key(self,text): return re.search(r'/confirm ([a-f0-9]{8})',text)[1]

    def test_write_requires_explicit_confirm_and_is_idempotent(self):
        assistant = Assistant(self.l,FakeCLI(('','stage',self.expense())))
        proposal = assistant.handle(OWNER,'Расход','telegram:1')
        self.assertEqual(self.l.balances('2026-10-02')[self.a],100000)
        key = self.key(proposal)
        assistant.handle(OWNER,'/confirm '+key,'telegram:2')
        assistant.handle(OWNER,'/confirm '+key,'telegram:3')
        self.assertEqual(self.l.balances('2026-10-02')[self.a],90000)
        self.assertEqual(len(self.l.snapshot()['operations']),1)

    def test_all_other_mutations_are_previewed_on_copy(self):
        assistant = Assistant(self.l,FakeCLI(('','account',{'name':'Новый','opening':'500.00','as_of':'2026-10-01'})))
        p = assistant.handle(OWNER,'Добавь счёт','tg:1')
        self.assertEqual(len(self.l.snapshot()['accounts']),1)
        assistant.handle(OWNER,'/confirm '+self.key(p),'tg:2')
        self.assertEqual(len(self.l.snapshot()['accounts']),2)

    def test_ambiguous_receipt_not_proposed(self):
        data = self.expense();data['data']['ambiguities']=['100 или 1000?']
        assistant = Assistant(self.l,FakeCLI(('','stage',data)))
        reply = assistant.handle(OWNER,'Чек','tg:1')
        self.assertNotIn('/confirm',reply)
        self.assertFalse(self.l.snapshot()['operations'])

    def test_similar_receipt_requires_second_explicit_confirm(self):
        assistant = Assistant(self.l,FakeCLI(('','stage',self.expense()),('','stage',self.expense())))
        a = assistant.handle(OWNER,'Расход','tg:1')
        assistant.handle(OWNER,'/confirm '+self.key(a),'tg:2')
        b = assistant.handle(OWNER,'Ещё расход','tg:3')
        warning = assistant.handle(OWNER,'/confirm '+self.key(b),'tg:4')
        self.assertEqual(len(self.l.snapshot()['operations']),1)
        self.assertIn('похожая',warning)
        assistant.handle(OWNER,'/confirm '+self.key(warning),'tg:5')
        self.assertEqual(len(self.l.snapshot()['operations']),2)

    def test_context_survives_assistant_restart(self):
        first = Assistant(self.l,FakeCLI(('Уточните счёт','none',{})))
        first.handle(OWNER,'Расход 100 рублей','tg:1')
        cli = FakeCLI(('Понятно','none',{}))
        Assistant(self.l,cli).handle(OWNER,'Карта','tg:2')
        self.assertIn('Расход 100 рублей',cli.prompts[0])

    def test_cli_failure_does_not_mutate_balance(self):
        assistant = Assistant(self.l,FakeCLI(CliError('Таймаут CLI')))
        self.assertIn('Таймаут',assistant.handle(OWNER,'Тест','tg:1'))
        self.assertFalse(self.l.snapshot()['operations'])

    def test_access_private_owner_only_and_update_dedup(self):
        cli = FakeCLI(('Привет','none',{}))
        transport = FakeTelegram()
        bot = Bot(self.l,Assistant(self.l,cli),transport)
        bot.ingest([update(1,'Секрет',user=77),update(2,'Секрет',group=True),update(3,'Привет')])
        self.assertTrue(bot.process_one());self.assertFalse(bot.process_one())
        bot.ingest([update(3,'Привет')]);self.assertFalse(bot.process_one())
        bot.ingest([update(1,'Старый повтор',user=77)])
        self.assertEqual(len(transport.sent),1)
        self.assertEqual(transport.sent[0][0],OWNER)
        self.assertEqual(self.l.rows("SELECT value FROM local_meta WHERE key='offset'")[0]['value'],'4')

    def test_delivery_failure_not_retried(self):
        bot = Bot(self.l,Assistant(self.l,FakeCLI(('Ответ','none',{}))),FakeTelegram(fail=True))
        bot.ingest([update(1,'Привет')]);bot.process_one()
        self.assertEqual(self.l.rows('SELECT state FROM local_updates')[0]['state'],'uncertain')
        self.assertFalse(bot.process_one())

    def test_restart_does_not_repeat_interrupted_confirmation(self):
        bot = Bot(self.l,Assistant(self.l,FakeCLI()),FakeTelegram())
        bot.ingest([update(1,'/confirm ABC')])
        with self.l.db: self.l.db.execute("UPDATE local_updates SET state='processing'")
        bot.recover()
        self.assertFalse(bot.process_one())
        self.assertEqual(self.l.rows('SELECT state FROM local_updates')[0]['state'],'uncertain')

    def test_cli_has_no_bypass_and_no_shell_or_inherited_tools(self):
        with patch('local_finance.codex_cli.executable',return_value='codex-test.exe'):
            command = Codex().command('work','schema','output')
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
        self.assertIn('read-only',command)
        self.assertIn('features.shell_tool=false',command)
        self.assertIn('features.plugins=false',command)
        self.assertIn('features.apps=false',command)
        self.assertEqual(command[-1],'-')

    def test_cancel_and_new_context_keep_finances(self):
        assistant = Assistant(self.l,FakeCLI(('','stage',self.expense())))
        p = assistant.handle(OWNER,'Расход','tg:1')
        assistant.handle(OWNER,'/new','tg:2')
        self.assertIn('уже обработано',assistant.handle(OWNER,'/confirm '+self.key(p),'tg:3'))
        self.assertEqual(self.l.balances('2026-10-02')[self.a],100000)

    def test_arbitrary_telegram_command_never_dispatched(self):
        assistant = Assistant(self.l,FakeCLI(('','exec_command',{'cmd':'anything'})))
        self.assertIn('отклонён',assistant.handle(OWNER,'Выполни shell','tg:1'))
        self.assertFalse(self.l.snapshot()['operations'])

    def test_auth_refresh_preserved_without_overwriting_new_login(self):
        with tempfile.TemporaryDirectory() as temp:
            original,updated = Path(temp)/'original.json',Path(temp)/'temporary.json'
            original.write_text('{"synthetic":"old"}')
            baseline = hashlib.sha256(original.read_bytes()).digest()
            updated.write_text('{"synthetic":"refreshed"}')
            sync_refreshed_login(original,updated,baseline)
            self.assertEqual(json.loads(original.read_text())['synthetic'],'refreshed')
            original.write_text('{"synthetic":"other-process-login"}')
            sync_refreshed_login(original,updated,baseline)
            self.assertEqual(json.loads(original.read_text())['synthetic'],'other-process-login')

    def test_local_reminders_target_owner_and_do_not_repeat(self):
        self.l.schedule('Платёж','expense','100.00',self.a,date='2026-10-05',lead_days=3)
        transport = FakeTelegram()
        bot = Bot(self.l,Assistant(self.l,FakeCLI()),transport)
        class FixedDate(datetime):
            @classmethod
            def now(cls,tz=None): return datetime(2026,10,3,10,tzinfo=timezone(timedelta(hours=5)))
        with patch('local_finance.bot.datetime',FixedDate):
            bot.check_reminders()
            bot.next_reminder_check=0
            bot.check_reminders()
        self.assertEqual(len(transport.sent),1)
        self.assertEqual(transport.sent[0][0],OWNER)

    def test_default_local_notice_starts_at_0900_yekaterinburg_day_before(self):
        self.l.schedule('Платёж','expense','100.00',self.a,date='2026-10-20')
        transport = FakeTelegram()
        bot = Bot(self.l,Assistant(self.l,FakeCLI()),transport)
        class FixedDate(datetime):
            hour_value=8
            @classmethod
            def now(cls,tz=None): return datetime(2026,10,19,cls.hour_value,59 if cls.hour_value==8 else 0,tzinfo=timezone(timedelta(hours=5)))
        with patch('local_finance.bot.datetime',FixedDate),patch.dict('os.environ',{'FINANCE_REMINDER_HOUR':'9'}):
            bot.check_reminders()
            self.assertEqual(transport.sent,[])
            FixedDate.hour_value=9
            bot.check_reminders()
            bot.next_reminder_check=0
            bot.check_reminders()
        self.assertEqual(len(transport.sent),1)
        self.assertEqual(transport.sent[0][0],OWNER)

    def test_real_runner_timeout_is_sanitized_and_cleans_credentials(self):
        class TimedOut:
            pid=99999999
            def __init__(self): self.calls=0
            def communicate(self,*args,**kwargs):
                self.calls+=1
                if self.calls==1: raise subprocess.TimeoutExpired('redacted',1)
                return b'',b''
            def kill(self): pass
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'auth.json').write_text('{"synthetic":"login"}')
            cli=Codex(timeout=1);cli.original_home=root
            with patch('local_finance.codex_cli.executable',return_value='codex-test.exe'),patch('local_finance.codex_cli.subprocess.Popen',return_value=TimedOut()) as launch,patch('local_finance.codex_cli.subprocess.run'):
                with self.assertRaisesRegex(CliError,'таймаут'): cli.plan('Только тест')
            self.assertEqual(list(root.glob('finance-cli-*')),[])
            self.assertEqual(json.loads((root/'auth.json').read_text())['synthetic'],'login')
            self.assertNotIn('TELEGRAM_BOT_TOKEN',launch.call_args.kwargs['env'])


if __name__=='__main__': unittest.main()
