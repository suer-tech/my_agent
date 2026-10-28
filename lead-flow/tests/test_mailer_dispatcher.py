from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from mailer.config import MailConfig
from mailer.dispatcher import dispatch
from mailer.store import DispatchStore
from mailer.transport import SendResult


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=DispatchStore(Path(self.temp.name)/'dispatch.sqlite3')
        self.config=MailConfig(enabled=True,send_enabled=True)
        self.message={'id':'reviewed-1','claim_token':'c'*40,'message_id':'<reviewed-1@lead-flow.invalid>',
            'from':self.config.sender,'to':'buyer@example.org','subject':'ИИ: резюме и интервью',
            'body':'Здравствуйте!\n\nТочный одобренный текст письма.\nКому показать пример?'}
        self.sent=[]

    def sender(self,message,config):
        self.assertEqual(self.store.pending()[0]['state'],'started')
        self.sent.append(dict(message))
        return SendResult('accepted',message['message_id'],'SMTP DATA 250 accepted')

    def execute(self,message=None,**kwargs):
        with self.store.lock():
            return dispatch(self.message if message is None else message,self.config,self.store,
                            sender=kwargs.get('sender',self.sender))

    def test_exact_approved_contact_and_text_are_sent_and_receipted(self):
        result=self.execute();self.assertTrue(result.attempted);self.assertEqual(result.result.outcome,'accepted')
        self.assertEqual(self.sent,[self.message])
        saved=self.store.pending()[0];self.assertEqual(saved['state'],'pending_ack')
        receipt=json.loads(saved['result_json']);self.assertEqual(receipt['outcome'],'accepted')
        self.assertEqual(receipt['provider_message_id'],self.message['message_id'])

    def test_duplicate_or_alternate_id_same_message_cannot_resend(self):
        self.execute()
        for message in [self.message,self.message|{'id':'other'}]:
            self.assertEqual(self.execute(message).reason,'duplicate_claim_no_resend')
        self.assertEqual(len(self.sent),1)

    def test_interrupted_recorded_attempt_is_never_replayed(self):
        self.store.register(self.message)
        self.assertEqual(self.execute().reason,'duplicate_claim_no_resend');self.assertFalse(self.sent)

    def test_unknown_transport_or_exception_stays_uncertain(self):
        for sender in [lambda *_:None,lambda *_:(_ for _ in ()).throw(OSError('private error'))]:
            with self.subTest(sender=sender):
                message=self.message|{'id':str(id(sender)),'message_id':f'<{id(sender)}@lead-flow.invalid>'}
                result=self.execute(message,sender=sender);self.assertEqual(result.result.outcome,'uncertain')
                self.assertFalse(self.execute(message,sender=sender).attempted)

    def test_receipt_write_failure_cannot_duplicate_smtp(self):
        with patch.object(self.store,'result',side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):self.execute()
        self.assertFalse(self.execute().attempted);self.assertEqual(len(self.sent),1)

    def test_disabled_and_invalid_claim_do_not_call_smtp(self):
        self.config=replace(self.config,send_enabled=False)
        self.assertEqual(self.execute().reason,'sending_disabled');self.assertFalse(self.store.has_attempts())
        self.config=replace(self.config,send_enabled=True)
        self.assertEqual(self.execute({}).reason,'invalid_claim_message');self.assertFalse(self.store.has_attempts())

    def test_invalid_address_or_header_injection_is_recorded_without_smtp(self):
        for i,patches in enumerate([{'to':'bad\nBcc: other@example.org'},{'subject':'Hello\r\nBcc: other@example.org'}]):
            message=self.message|patches|{'id':f'invalid-{i}','message_id':f'<invalid-{i}@lead-flow.invalid>'}
            result=self.execute(message);self.assertEqual(result.result.outcome,'failed_before_send')
        self.assertFalse(self.sent)
