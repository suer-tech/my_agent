import unittest
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from urllib.parse import urlsplit, parse_qs

from acquisition import Reply
from mailer.config import MailConfig
from mailer.transport import compose, UNSUBSCRIBE_BODY
from mailer.inbox import parse_reply


class UnsubscribeTests(unittest.TestCase):
    def test_headers_and_explicit_response_roundtrip(self):
        mid = '<leadflow-' + 'a'*32 + '@mail.ru>'
        draft = {'from':'ai.studio.ufa@mail.ru','to':'buyer@example.org',
                 'message_id':mid,'subject':'OCR','body':'Исходное предложение'}
        message=BytesParser(policy=policy.default).parsebytes(compose(draft,MailConfig()))
        self.assertEqual(message['From'].addresses[0].addr_spec,draft['from'])
        self.assertEqual(message['From'].addresses[0].display_name,'AI Studio')
        self.assertEqual(str(message['Reply-To']),draft['from'])
        self.assertEqual(str(message['Precedence']),'bulk')
        self.assertEqual(message.get_content().strip(),draft['body'])
        link=urlsplit(str(message['List-Unsubscribe']).strip('<>'))
        self.assertEqual(link.path,draft['from'])
        query=parse_qs(link.query)
        response=EmailMessage();response['From']=draft['to'];response['To']=draft['from']
        response['Subject']=query['subject'][0];response['Message-ID']='<owner-request@example.org>'
        response.set_content(query['body'][0])
        parsed=parse_reply(response.as_bytes(),digest='test')
        self.assertEqual(parsed['classification'],'refused')
        self.assertEqual(parsed['in_reply_to'],mid)
        self.assertEqual(parsed['classification_quote'],UNSUBSCRIBE_BODY)
        Reply.model_validate(parsed)

    def test_forward_auto_reply_or_conflicting_thread_is_not_a_refusal(self):
        mid='<leadflow-'+'a'*32+'@mail.ru>'
        for subject,body,auto,ref in [
            ('Re: Отписка OCR '+mid,UNSUBSCRIBE_BODY,'',''),
            ('Отписка OCR '+mid,'Цитата: '+UNSUBSCRIBE_BODY,'',''),
            ('Отписка OCR '+mid,UNSUBSCRIBE_BODY,'auto-replied',''),
            ('Отписка OCR '+mid,UNSUBSCRIBE_BODY,'','<other@mail.ru>')]:
            with self.subTest(subject=subject,auto=auto,ref=ref):
                m=EmailMessage();m['From']='buyer@example.org';m['Subject']=subject
                if auto:m['Auto-Submitted']=auto
                if ref:m['In-Reply-To']=ref
                m.set_content(body)
                self.assertNotEqual(parse_reply(m.as_bytes(),digest='test')['classification'],'refused')
