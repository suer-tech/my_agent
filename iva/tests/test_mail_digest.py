import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from iva_integration import mail_digest as d


class Digest(unittest.TestCase):
    def poll(self,cursor,fail=False):
        class Client:
            def response(self,key):return 'OK',[b'105']
            def uid(self,*args):return 'OK',[b'100 101 102 103 104']
            def logout(self):pass
        def summarize(message):
            if fail:raise RuntimeError('model offline')
            return 'Body was read: '+message['text']
        with patch.object(d.mail,'credentials',return_value={}),patch.object(d.mail,'selected',return_value='v'),patch.object(d.mail,'fetch',side_effect=lambda c,uid,*a:('',b'From: sender@example.org\nSubject: Test\n\nActual body',80)),patch.object(d,'event'):
            return d.poll({'folder':'INBOX'},cursor,connector=lambda _:Client(),summarizer=summarize)
    def test_baseline_does_not_replay_old_mail(self):
        result=self.poll({});self.assertFalse(result['notify']);self.assertEqual(result['cursor']['last_uid'],104)
    def test_reads_new_bodies_skips_already_reported_and_dedupes(self):
        result=self.poll({'uidvalidity':'v','last_uid':100,'reported_uids':[102,103]})
        self.assertEqual(result['processed_uids'],['101','104']);self.assertIn('Actual body',result['text'])
        second=self.poll(result['cursor']);self.assertFalse(second['notify']);self.assertEqual(second['processed_uids'],[])
    def test_failure_does_not_advance_cursor(self):
        result=self.poll({'uidvalidity':'v','last_uid':100},True)
        self.assertFalse(result['ok']);self.assertNotIn('cursor',result)
    def test_mailbox_recreation_requires_reconciliation(self):
        result=self.poll({'uidvalidity':'old','last_uid':100})
        self.assertFalse(result['ok']);self.assertNotIn('cursor',result)


if __name__=='__main__':unittest.main()
