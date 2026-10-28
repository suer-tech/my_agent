import copy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy import lead_research_control as control
from iva_integration import lead_research as lead


class LeadResearchReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / 'research.sqlite3'
        self.config = self.root / 'research.json'
        self.config.write_text(json.dumps({'enabled': True, 'mode': 'preview', 'campaign': 'ocr', 'secret_extra': 'DO_NOT_RETURN'}))
        self.run_id = 'a' * 32
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript('''CREATE TABLE runs(id,campaign,slot,state,model_calls,created,updated,error);
            CREATE TABLE candidates(run_id,domain,stage,error,result,lead_ack,draft_ack);
            CREATE TABLE pages(run_id,url,data);''')
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)', (self.run_id, 'ocr', '2026-10-26', 'completed', 7, '2026-10-26T10:00:00+00:00', '2026-10-26T10:01:00+00:00', None))
            payload = {'lead': {'company': 'Тест', 'qualification_status': 'needs_review', 'evidence': [{'source_url': 'https://example.org/contact', 'quote': 'Короткий источник', 'extra': 'HIDDEN'}]}, 'draft': {'subject': 'Вопрос', 'body': 'Контекстный вопрос о документах', 'contact_basis': 'owner_review', 'secret': 'HIDDEN'}}
            db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)', (self.run_id, 'example.org', 'preview', None, json.dumps(payload), None, None))
            db.execute('INSERT INTO pages VALUES(?,?,?)', (self.run_id, 'https://example.org', json.dumps({'ok':True,'text':'RAW_PAGE_NOT_RETURNED'})))
            db.commit()
        self.patches = [patch.object(lead, 'CONFIG', self.config), patch.object(lead, 'DATABASE', self.database), patch.object(lead, 'CONTROL', self.root / 'control.json'), patch.object(lead, 'CRM_PROOF', self.root / 'commissioning.json'), patch.object(lead, 'MAIL_CONFIG',self.root/'mail.json'), patch.object(lead,'MAIL_DATABASE',self.root/'mail.sqlite3'), patch.object(lead, '_services', return_value={'available': False})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def test_mail_status_uses_read_only_transport_journal_without_claims_or_bodies(self):
        (self.root/'mail.json').write_text(json.dumps({'enabled':True,'send_enabled':True,'password':'SECRET_MAIL_VALUE'}))
        path=self.root/'mail.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('CREATE TABLE attempts(state,result_json); CREATE TABLE runs(id,state,started_at,updated_at,summary_json);')
            db.execute('INSERT INTO attempts VALUES(?,?)',('acknowledged',json.dumps({'outcome':'accepted','claim_token':'SECRET_CLAIM_VALUE'})))
            db.execute('INSERT INTO attempts VALUES(?,?)',('pending_ack',json.dumps({'outcome':'uncertain'})))
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',('mail-one','completed','2026-10-26','2026-10-26',json.dumps({'accepted':1,'reason':'paced','body':'PRIVATE_BODY'})))
            db.commit()
        before=path.read_bytes();result=lead.status()['mailer']
        self.assertTrue(result['available']);self.assertEqual({'accepted':1,'uncertain':1},result['outcomes'])
        self.assertEqual('paced',result['last_run']['summary']['reason'])
        self.assertFalse(result['delivery_confirmed']);self.assertEqual(before,path.read_bytes())
        for secret in ('SECRET_MAIL_VALUE','SECRET_CLAIM_VALUE','PRIVATE_BODY'):self.assertNotIn(secret,json.dumps(result))

    def test_absent_mail_journal_is_not_reported_as_zero_verified_sends(self):
        result=lead.status()['mailer']
        self.assertFalse(result['available']);self.assertEqual({},result['outcomes'])
        self.assertFalse((self.root/'mail.sqlite3').exists())

    def test_current_copy_overlays_but_does_not_rewrite_historical_draft(self):
        ident='b'*32
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE candidates SET draft_ack=?,lead_ack=?',(json.dumps({'id':ident}),json.dumps({'lead_id':1})))
            original=db.execute('SELECT result FROM candidates').fetchone()[0]
            db.execute('CREATE TABLE outreach_copy_overrides(outreach_id,copy_version,draft_json,updated_at)')
            db.execute('INSERT INTO outreach_copy_overrides VALUES(?,?,?,?)',(ident,'ai-benchmark-v1',json.dumps({'subject':'Новое предложение ИИ','body':'Новый проверенный текст','claim_token':'SECRET_CLAIM'}),'2026-10-26'))
            db.commit()
        item=lead.candidates()['items'][0]
        self.assertEqual('Новый проверенный текст',item['draft']['body'])
        self.assertEqual('ai-benchmark-v1',item['copy_revision']['version'])
        self.assertNotIn('SECRET_CLAIM',json.dumps(item))
        with closing(sqlite3.connect(self.database)) as db:self.assertEqual(original,db.execute('SELECT result FROM candidates').fetchone()[0])

    def test_status_returns_real_checkpoints_without_private_config_or_page_body(self):
        result = lead.execute({'action': 'status'})
        self.assertEqual(result['runs'][0]['pages'], 1)
        self.assertEqual(result['runs'][0]['pages_attempted'], 1)
        self.assertEqual(result['runs'][0]['pages_readable'], 1)
        self.assertEqual(result['runs'][0]['pages_failed'], 0)
        self.assertEqual(result['runs'][0]['drafts_prepared'], 1)
        self.assertEqual(result['runs'][0]['model_calls'], 7)
        self.assertFalse(result['email_sending'])
        self.assertNotIn('DO_NOT_RETURN', json.dumps(result))
        self.assertNotIn('RAW_PAGE_NOT_RETURNED', json.dumps(result))

    def test_attempted_pages_are_not_reported_as_read_and_skip_reasons_remain_visible(self):
        with closing(sqlite3.connect(self.database)) as db:
            for index in range(4):
                db.execute('INSERT INTO pages VALUES(?,?,?)',(self.run_id,f'https://example.org/read-{index}',json.dumps({'ok':True,'text':'Read content'})))
            for index,reason in enumerate(('access_denied','timeout')):
                db.execute('INSERT INTO pages VALUES(?,?,?)',(self.run_id,f'https://example.org/error-{index}',json.dumps({'ok':False,'text':'','error_category':reason})))
            db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)',(self.run_id,'missing.example.org','rejected','contact_unverified',None,None,None))
            db.commit()
        result = lead.execute({'action':'status'})
        run = result['runs'][0]
        self.assertEqual((run['pages'],run['pages_attempted'],run['pages_readable'],run['pages_failed']),(7,7,5,2))
        self.assertEqual(run['page_failure_reasons'],[{'reason':'access_denied','count':1},{'reason':'timeout','count':1}])
        self.assertEqual(run['skip_reasons'],[{'stage':'rejected','reason':'contact_unverified','count':1}])
        self.assertEqual(run['drafts_prepared'],1)
        self.assertFalse(run['email_sent'])
        self.assertIn('oneshot',result['service_explanation'])

    def test_compacted_readable_body_is_not_reclassified_as_failed(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE pages SET data=?',(json.dumps({'ok':True,'text':'','body_compacted_at':'2026-11-10T10:00:00+00:00'}),))
            db.commit()
        run = lead.execute({'action':'status'})['runs'][0]
        self.assertEqual((run['pages_readable'],run['pages_failed'],run['pages_compacted']),(1,0,1))

    def test_null_draft_is_not_counted_and_candidate_read_stays_available(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE candidates SET result=?',(json.dumps({'lead':{'company':'Needs review'},'draft':None}),))
            db.execute('UPDATE pages SET data=?',('corrupt metadata',))
            db.commit()
        run = lead.execute({'action':'status'})['runs'][0]
        self.assertEqual(run['drafts_prepared'],0)
        self.assertEqual(run['pages_failed'],1)
        self.assertEqual(run['page_failure_reasons'],[{'reason':'invalid_page_metadata','count':1}])
        self.assertIsNone(lead.execute({'action':'candidates'})['items'][0]['draft']['body'])

    def test_candidate_is_draft_and_not_mail_delivery(self):
        result = lead.execute({'action': 'candidates', 'limit': 1})
        self.assertEqual(result['run_id'], self.run_id)
        self.assertEqual(result['items'][0]['draft']['subject'], 'Вопрос')
        self.assertFalse(result['items'][0]['crm_draft_acknowledged'])
        self.assertTrue(result['qualification_is_not_purchase_intent'])
        self.assertNotIn('HIDDEN', json.dumps(result))

    def test_ingest_and_active_socket_are_configuration_not_authenticated_api(self):
        self.config.write_text(json.dumps({'enabled':True,'mode':'ingest','campaign':'ocr',
            'crm_url':lead.CRM_ORIGIN,'crm_transport':'iva_vpn'}))
        services = {'available':True,'units':{lead.CRM_UNIT:{'ActiveState':'active'}}}
        before = self.database.read_bytes()
        with patch.object(lead,'_services',return_value=services), patch.object(lead,'_present',return_value=True) as metadata:
            result = lead.status()
        crm = result['crm']
        self.assertTrue(crm['configured'])
        self.assertTrue(crm['export_enabled_in_config'])
        self.assertEqual(crm['credential_source'],'private_transport_helper')
        self.assertTrue(crm['helper_present'])
        self.assertTrue(crm['socket_active'])
        self.assertEqual(crm['authentication_status'],'unverified')
        self.assertFalse(crm['current_connectivity_verified'])
        self.assertFalse(crm['network_checks_performed'])
        self.assertFalse(crm['credential_contents_checked'])
        self.assertIsNone(crm['last_confirmed_export'])
        self.assertEqual([call.args[0] for call in metadata.call_args_list],[lead.CRM_HELPER,lead.CRM_SOCKET])
        self.assertEqual(self.database.read_bytes(),before)

    def test_suppressed_archived_malformed_or_missing_id_ack_is_not_a_crm_card(self):
        invalid = ({'suppressed':True},{'lead_id':12,'suppressed':True},
            {'lead_id':12,'archived':True},{'lead_id':True},{'lead_id':0},
            {'lead_id':'12'},{},[],None)
        for ack in invalid:
            with closing(sqlite3.connect(self.database)) as db:
                db.execute('UPDATE candidates SET lead_ack=?,draft_ack=?',(json.dumps(ack),json.dumps({'id':'a'*32})))
                db.commit()
            with self.subTest(ack=ack):
                item = lead.candidates()['items'][0]
                crm = lead.status()['crm']
                self.assertFalse(item['crm_card_acknowledged'])
                self.assertFalse(item['crm_draft_acknowledged'])
                self.assertEqual(crm['authentication_status'],'unverified')
                self.assertIsNone(crm['last_confirmed_export'])
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE candidates SET lead_ack=?',('invalid JSON',));db.commit()
        self.assertFalse(lead.candidates()['items'][0]['crm_card_acknowledged'])

    def test_valid_own_database_ack_is_historical_not_current_connectivity_or_mail(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE candidates SET lead_ack=?,draft_ack=?',
                (json.dumps({'lead_id':12,'created':False,'replayed':True,'secret':'HIDDEN'}),json.dumps({'id':'b'*32,'state':'draft'})))
            db.commit()
        state = lead.status()
        crm = state['crm']
        self.assertEqual(crm['authentication_status'],'historical_card_acknowledgement')
        self.assertFalse(crm['current_connectivity_verified'])
        self.assertEqual(crm['last_confirmed_export']['run_id'],self.run_id)
        self.assertEqual(crm['last_confirmed_export']['card_acknowledgements'],1)
        self.assertEqual(crm['last_confirmed_export']['draft_acknowledgements'],1)
        self.assertIsNone(crm['last_confirmed_export']['export_timestamp'])
        self.assertEqual(state['runs'][0]['crm_card_acknowledgements'],1)
        self.assertTrue(lead.candidates()['items'][0]['crm_draft_acknowledged'])
        self.assertFalse(state['email_sending'])
        self.assertNotIn('HIDDEN',json.dumps(state))
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE candidates SET draft_ack=?',(json.dumps({'id':'garbage'}),));db.commit()
        self.assertFalse(lead.candidates()['items'][0]['crm_draft_acknowledged'])

    def test_metadata_never_opens_the_inspected_file(self):
        helper = self.root/'helper.py'
        helper.write_text('PRIVATE_CONTENT_NOT_READ')
        with patch.object(type(helper),'read_text',side_effect=AssertionError('file content must not be read')), \
                patch.object(type(helper),'read_bytes',side_effect=AssertionError('file content must not be read')):
            self.assertTrue(lead._present(helper,'file'))
            self.assertFalse(lead._present(helper,'socket'))
            self.assertFalse(lead._present(self.root/'missing','file'))

    def test_missing_database_does_not_create_it(self):
        self.database.unlink()
        result = lead.execute({'action': 'status'})
        self.assertFalse(result['database_present'])
        self.assertEqual(result['runs'], [])
        self.assertFalse(self.database.exists())

    def test_unknown_action_paths_and_invalid_limit_rejected_before_execution(self):
        with patch.object(lead.subprocess, 'run') as runner:
            for data in ({'action': 'run; rm'}, {'action': 'status', 'path': '/etc/passwd'}, {'action': 'candidates', 'limit': True}, {'action': 'candidates', 'run_id': "' OR 1=1"}):
                with self.subTest(data=data), self.assertRaises(ValueError):
                    lead.execute(data)
            runner.assert_not_called()

    def test_database_connection_is_query_only(self):
        db = lead._db()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM runs')
        finally:
            db.close()


class CommissioningProofTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime(2026,10,26,12,tzinfo=timezone.utc)
        self.config = {'crm_transport':'iva_vpn','crm_url':lead.CRM_ORIGIN}
        self.proof = {'version':1,'origin':lead.CRM_ORIGIN,'transport':'iva_vpn',
            'proofscope':'scoped_ingest','verified_at':(self.clock-timedelta(minutes=1)).isoformat(),
            'ingest_http_status':200,'lead_id':123,'replay_http_status':200,
            'replay_lead_id':123,'replay_confirmed':True}

    def inspect(self, change=None):
        value = {**self.proof,**(change or {})}
        with patch.object(lead,'_proof_bytes',return_value=json.dumps(value).encode()):
            return lead._commissioning(self.config,self.clock)

    def test_valid_proof_is_limited_historical_ingest_not_current_api_health(self):
        value = self.inspect()
        self.assertEqual(value['proof_status'],'verified')
        self.assertEqual(value['proofscope'],'scoped_ingest')
        self.assertEqual(value['last_verified_at'],self.proof['verified_at'])
        self.assertEqual(value['current_api_health'],'not_checked')
        self.assertNotIn('lead_id',value)
        self.assertNotIn('connected',value)

    def test_wrong_scope_origin_transport_id_http_or_extra_fields_rejected(self):
        for change in ({'origin':'https://evil.example'},{'transport':'direct'},
                {'proofscope':'email_sent'},{'lead_id':True},{'replay_lead_id':124},
                {'replay_confirmed':False},{'ingest_http_status':401},
                {'replay_http_status':302},{'secret':'MUST_NOT_RETURN'},{'version':True}):
            with self.subTest(change=change):
                value = self.inspect(change)
                self.assertEqual(value['proof_status'],'invalid_or_unreadable')
                self.assertIsNone(value['last_verified_at'])
                self.assertNotIn('MUST_NOT_RETURN',json.dumps(value))

    def test_expired_future_naive_non_utc_and_malformed_dates_do_not_verify(self):
        cases = ((self.clock-timedelta(days=8)).isoformat(),'expired'), \
            ((self.clock+timedelta(seconds=1)).isoformat(),'invalid_or_unreadable'), \
            ('2026-10-26T10:00:00','invalid_or_unreadable'), \
            ('2026-10-26T10:00:00+05:00','invalid_or_unreadable'), \
            ('not-a-date','invalid_or_unreadable')
        for value, expected in cases:
            with self.subTest(value=value):
                result = self.inspect({'verified_at':value})
                self.assertEqual(result['proof_status'],expected)
                self.assertIsNone(result['last_verified_at'])

    def test_config_mismatch_never_reads_proof_and_missing_file_is_not_failure(self):
        for config in ({'crm_transport':'direct','crm_url':lead.CRM_ORIGIN},
                {'crm_transport':'iva_vpn','crm_url':'https://different.example'}):
            with patch.object(lead,'_proof_bytes',side_effect=AssertionError('must not read')):
                self.assertEqual(lead._commissioning(config,self.clock)['proof_status'],'not_applicable')
        with patch.object(lead,'_proof_bytes',side_effect=FileNotFoundError()):
            self.assertEqual(lead._commissioning(self.config,self.clock)['proof_status'],'absent')

    def test_proof_requires_root_owner_exact_0640_regular_file_and_safe_parents(self):
        good = dict(st_mode=stat.S_IFREG|0o640,st_uid=0,st_size=100)
        self.assertTrue(lead._proof_stat(SimpleNamespace(**good)))
        for change in ({'st_uid':1000},{'st_mode':stat.S_IFREG|0o644},
                {'st_mode':stat.S_IFREG|0o600},{'st_mode':stat.S_IFLNK|0o640},
                {'st_size':8193}):
            with self.subTest(change=change):
                self.assertFalse(lead._proof_stat(SimpleNamespace(**{**good,**change})))
        self.assertTrue(lead._proof_stat(SimpleNamespace(st_mode=stat.S_IFDIR|0o750,st_uid=0),directory=True))
        self.assertFalse(lead._proof_stat(SimpleNamespace(st_mode=stat.S_IFDIR|0o770,st_uid=0),directory=True))
        self.assertFalse(lead._proof_stat(SimpleNamespace(st_mode=stat.S_IFLNK|0o750,st_uid=0),directory=True))

    def test_file_replacement_between_stat_and_open_is_not_read(self):
        info = dict(st_mode=stat.S_IFREG|0o640,st_uid=0,st_size=2,st_dev=1,st_ino=10)
        fake_path = Mock()
        fake_path.parents = []
        fake_path.lstat.return_value = SimpleNamespace(**info)
        stream = Mock()
        manager = Mock()
        manager.__enter__ = Mock(return_value=stream)
        manager.__exit__ = Mock(return_value=False)
        with patch.object(lead,'CRM_PROOF',fake_path), patch.object(lead.os,'open',return_value=7), \
                patch.object(lead.os,'fdopen',return_value=manager), \
                patch.object(lead.os,'fstat',return_value=SimpleNamespace(**{**info,'st_ino':11})):
            with self.assertRaisesRegex(ValueError,'proof_changed'):
                lead._proof_bytes()
        stream.read.assert_not_called()


class OwnerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('CREATE TABLE local_updates(id,chat,state,metadata); CREATE TABLE local_telegram_messages(id,message_json);')
        self.owner = 123
        self.source = 'telegram:123:42:0'
        self.message = {'from': {'id': 123}, 'chat': {'id': 123, 'type': 'private'}, 'text': 'Ива, запусти поиск клиентов OCR', 'date': 12345}
        self.db.execute('INSERT INTO local_updates VALUES(42,123,\'processing\',\'{}\')')
        self.db.execute('INSERT INTO local_telegram_messages VALUES(42,?)', (json.dumps(self.message),))
        self.ledger = SimpleNamespace(rows=lambda query, args: [dict(row) for row in self.db.execute(query, args)])

    def tearDown(self):
        self.db.close()

    def test_direct_current_owner_request_uses_fixed_helper_and_stable_digest(self):
        response = SimpleNamespace(returncode=0, stdout='{"ok":true,"accepted":true,"email_sending":false}')
        with patch.object(lead.subprocess, 'run', return_value=response) as runner:
            request = {'action': 'run', 'instruction_quote': self.message['text']}
            self.assertTrue(lead.execute(request, self.ledger, self.owner, self.source)['accepted'])
            first = runner.call_args
            lead.execute(request, self.ledger, self.owner, self.source)
            self.assertEqual(first.kwargs['input'], runner.call_args.kwargs['input'])
            self.assertEqual(first.args[0], ['/usr/bin/sudo', '-n', lead.HELPER])
            self.assertEqual(set(json.loads(first.kwargs['input'])), {'action', 'request_id'})
            self.assertNotIn(self.message['text'], first.kwargs['input'])

    def test_old_forwarded_or_inexact_owner_message_cannot_start(self):
        with patch.object(lead.subprocess, 'run') as runner:
            with self.assertRaises(PermissionError):
                lead.execute({'action': 'run', 'instruction_quote': 'не тот текст'}, self.ledger, self.owner, self.source)
            self.db.execute("UPDATE local_updates SET state='done'")
            with self.assertRaises(PermissionError):
                lead.execute({'action': 'run', 'instruction_quote': self.message['text']}, self.ledger, self.owner, self.source)
            self.db.execute("UPDATE local_updates SET state='processing'")
            self.message['forward_origin'] = {'type': 'user'}
            self.db.execute('UPDATE local_telegram_messages SET message_json=?', (json.dumps(self.message),))
            with self.assertRaises(PermissionError):
                lead.execute({'action': 'run', 'instruction_quote': self.message['text']}, self.ledger, self.owner, self.source)
            runner.assert_not_called()

    def test_helper_error_does_not_expose_stderr(self):
        response = SimpleNamespace(returncode=1, stdout='', stderr='SECRET')
        with patch.object(lead.subprocess, 'run', return_value=response):
            result = lead.execute({'action': 'pause', 'instruction_quote': self.message['text']}, self.ledger, self.owner, self.source)
        self.assertFalse(result['ok'])
        self.assertNotIn('SECRET', json.dumps(result))


class FixedHelperTests(unittest.TestCase):
    def payload(self, action='run'):
        return {'action': action, 'request_id': 'b' * 64}

    def test_run_is_nonblocking_manual_only_and_idempotent(self):
        state, calls = {}, []
        def command(*args):
            calls.append(args)
            return 'inactive' if args[0] == 'show' else ''
        result = control.perform(self.payload(), state, command, lambda value: None)
        self.assertTrue(result['accepted'])
        self.assertFalse(result['completed'])
        self.assertIn(('start', '--no-block', control.MANUAL), calls)
        number = len(calls)
        replay = control.perform(self.payload(), state, command, lambda value: None)
        self.assertTrue(replay['replayed'])
        self.assertEqual(len(calls), number)

    def test_pause_never_stops_active_services_or_changes_config(self):
        calls = []
        result = control.perform(self.payload('pause'), {}, lambda *args: calls.append(args), lambda value: None)
        self.assertEqual(calls, [('disable', '--now', control.TIMER)])
        self.assertFalse(result['current_cycle_interrupted'])

    def test_resume_only_enables_timer_and_requires_worker_config_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'research.json'
            calls = []
            config.write_text('{"enabled":false}')
            with patch.object(control, 'CONFIG', config):
                result = control.perform(self.payload('resume'), {}, lambda *args: calls.append(args), lambda value: None)
                self.assertFalse(result['ok'])
                self.assertEqual(calls, [])
                config.write_text('{"enabled":true}')
                result = control.perform(self.payload('resume'), {}, lambda *args: calls.append(args), lambda value: None)
                self.assertEqual(calls, [('enable', '--now', control.TIMER)])
                self.assertFalse(result['immediate_run_requested'])

    def test_running_service_does_not_start_second_worker(self):
        calls = []
        result = control.perform(self.payload(), {}, lambda *args: calls.append(args) or 'activating', lambda value: None)
        self.assertEqual(result['state'], 'already_running')
        self.assertTrue(all(args[0] == 'show' for args in calls))

    def test_interrupted_control_is_not_replayed(self):
        state = {'requests': {'b' * 64: {'action': 'run', 'state': 'inflight'}}}
        with patch.object(control, 'command') as command:
            result = control.perform(self.payload(), state, command, lambda value: None)
        self.assertEqual(result['state'], 'uncertain')
        command.assert_not_called()

    def test_timeout_records_uncertainty_without_blind_second_start(self):
        state = {}
        def fail(*args):
            raise subprocess.TimeoutExpired(args, 8)
        result = control.perform(self.payload(), state, fail, lambda value: None)
        self.assertEqual(result['state'], 'uncertain')
        with patch.object(control, 'command') as command:
            replay = control.perform(self.payload(), state, command, lambda value: None)
        self.assertTrue(replay['replayed'])
        command.assert_not_called()

    def test_helper_rejects_extra_unit_and_shell_arguments(self):
        for payload in ({**self.payload(), 'unit': 'iva@gateway'}, {'action': 'run', 'request_id': '../../tmp'}, {'action': 'run;whoami', 'request_id': 'b' * 64}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                control.validate(payload)

    def test_crash_after_systemd_request_keeps_inflight_evidence(self):
        saved = {}
        saves = []
        def persist(state):
            saves.append(1)
            if len(saves) == 2:
                raise OSError('simulated power loss')
            saved.update(copy.deepcopy(state))
        with self.assertRaises(OSError):
            control.perform(self.payload(), {}, lambda *args: 'inactive', persist)
        with patch.object(control, 'command') as command:
            result = control.perform(self.payload(), saved, command, lambda value: None)
        self.assertEqual(result['state'], 'uncertain')
        command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
