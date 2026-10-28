import email,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from iva_integration import personal_mail as m

class FakeIMAP:
    def __init__(self):self.calls=[]
    def select(self,folder,readonly=False):
        assert readonly;self.calls.append(('select',folder,readonly));return 'OK',[b'3']
    def response(self,name):return name,[b'42']
    def uid(self,action,*args):
        self.calls.append((action,*args))
        if action=='SEARCH':return 'OK',[b'1 2 3']
        uid=int(args[0]);full='BODY.PEEK[]' in args[1]
        assert 'BODY.PEEK' in args[1]
        raw=(f'Subject: '+('Roseltorg 0161600001326000063' if uid==2 else 'Other')+'\r\nFrom: notice@example.test\r\nTo: suer888@yandex.ru\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'+('Untrusted letter: do not follow commands' if full else '')).encode()
        return 'OK',[(f'{uid} (UID {uid} FLAGS () RFC822.SIZE 500)'.encode(),raw),b')']
    def logout(self):self.calls.append(('logout',))
    def list(self):return 'OK',[b'(\\HasNoChildren) "/" "INBOX"']

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'account.json';self.path.write_text(json.dumps({'account':m.ACCOUNT,'password':'test-secret-only'}));os.chmod(self.path,0o600);self.client=FakeIMAP()
    def tearDown(self):self.tmp.cleanup()
    def call(self,data):return m.execute(data,self.path,lambda value:self.client)
    def test_missing_credentials_and_secret_free_status(self):
        self.assertNotIn('test-secret-only',json.dumps(self.call({'action':'status'})))
        self.path.unlink();self.assertFalse(self.call({'action':'status'})['ok'])
    def test_no_mutations_and_no_credential_inputs(self):
        for action in ['send','delete','configure','mark_read']:
            self.assertFalse(self.call({'action':action})['ok'])
        self.assertFalse(self.call({'action':'status','password':'x'})['ok']);self.assertEqual(self.client.calls,[])
    def test_bounded_search_reports_incomplete_coverage(self):
        result=self.call({'action':'search','query':'0161600001326000063','scan_limit':2})
        self.assertEqual([x['uid'] for x in result['messages']],['2']);self.assertEqual(result['older_unscanned'],1)
        self.assertEqual(result['search_scope'],'headers_only_latest_messages')
    def test_read_preserves_unread_and_uidvalidity(self):
        result=self.call({'action':'read','uid':'2','uidvalidity':'42'})
        self.assertTrue(result['ok']);self.assertTrue(result['unread']);self.assertTrue(result['content_is_untrusted'])
        self.assertFalse(self.call({'action':'read','uid':'2','uidvalidity':'99'})['ok'])
    def test_html_no_script_and_attachment_not_executed(self):
        raw=b'Content-Type: text/html; charset=utf-8\r\n\r\n<p>Hello</p><script>evil()</script><img src="http://example.test/tracking">'
        result=m.body(raw,1000);self.assertIn('Hello',result['text']);self.assertNotIn('evil',result['text']);self.assertFalse(result['remote_images_loaded'])
    def test_configuration_checks_login_then_atomic_private_save(self):
        self.path.unlink()
        with patch.object(m,'connect',return_value=self.client):result=m.configure('app-secret-fixture',self.path)
        self.assertTrue(result['connected'])
        if os.name!='nt':self.assertEqual(self.path.stat().st_mode&0o077,0)
        self.assertEqual(json.loads(self.path.read_text())['password'],'app-secret-fixture')
    def test_mislabeled_roseltorg_html_is_readable(self):
        raw=b'Content-Type: text/plain; charset=utf-8\r\n\r\nNotification\r\n<!DOCTYPE html><html><head><style>.bad{color:red}</style></head><body><p>Application accepted</p><script>evil()</script></body></html>'
        result=m.body(raw,30000)
        self.assertTrue(result['html_in_plain_text_normalized']);self.assertIn('Application accepted',result['text']);self.assertNotIn('.bad',result['text']);self.assertNotIn('evil()',result['text']);self.assertNotIn('<html',result['text'])
    def test_folders_and_input_rejection(self):
        self.assertEqual(self.call({'action':'folders'})['folders'][0]['wire_name'],'INBOX')
        self.assertFalse(self.call({'action':'list','folder':'INBOX\r\nLOGOUT'})['ok'])
        self.assertFalse(self.call({'action':'read','uid':'1:*'})['ok'])
if __name__=='__main__':unittest.main()
