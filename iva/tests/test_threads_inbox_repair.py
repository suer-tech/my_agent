import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iva_integration import threads_inbox as inbox


EMPTY={'empty':True,'gate':False,'challenge':False,'rows':[]}


class Locator:
    def is_visible(self):return False


class Page:
    def __init__(self,failures=None,results=None,restore_failure=False):
        self.url='https://www.threads.com/'
        self.failures=dict(failures or {})
        self.results=results or {}
        self.restore_failure=restore_failure
        self.visits=[];self.timeouts=[];self.waits=[]
    def is_closed(self):return False
    def goto(self,url,**kwargs):
        self.visits.append(url);self.timeouts.append(kwargs['timeout'])
        suffix=url.removeprefix('https://www.threads.com/messages')
        if url=='https://www.threads.com/' and self.restore_failure:
            raise TimeoutError('secret page restoration URL')
        if self.failures.get(suffix,0):
            self.failures[suffix]-=1
            raise TimeoutError('secret preview and authentication URL')
        self.url=url
    def wait_for_timeout(self,value):self.waits.append(value)
    def wait_for_function(self,script,**kwargs):
        self.timeouts.append(kwargs['timeout'])
        self.ready_script=script
    def get_by_text(self,*args,**kwargs):return Locator()
    def evaluate(self,script):
        suffix=self.url.removeprefix('https://www.threads.com/messages')
        return copy.deepcopy(self.results.get(suffix,EMPTY))


class Context:
    def __init__(self,page):self.pages=[page]


class InboxRepair(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.wake=patch.object(inbox,'wake');self.wake.start()
    def tearDown(self):self.wake.stop();self.tmp.cleanup()
    def test_transient_navigation_retries_same_folder_once(self):
        page=Page(failures={'/requests':1});folders=[];events=[]
        result=inbox.scan(page,on_folder=lambda rows:folders.append(rows),on_event=events.append)
        self.assertEqual(result['folders'],['inbox','requests','hidden'])
        self.assertEqual(len(folders),3)
        self.assertEqual(page.visits.count('https://www.threads.com/messages/requests'),2)
        self.assertEqual([e['folder'] for e in events if e['state']=='retry'],['requests'])
    def test_second_timeout_is_error_and_no_third_navigation(self):
        page=Page(failures={'':3})
        state=inbox.check(Context(page),self.root,force=True)
        self.assertEqual(state['state'],'error')
        self.assertEqual(state['error_code'],'timeout')
        self.assertEqual(state['folder'],'inbox')
        self.assertEqual(state['attempt'],2)
        self.assertEqual(page.visits.count('https://www.threads.com/messages'),2)
        self.assertNotIn('visible_conversations',state)
    def test_successful_read_survives_restore_timeout(self):
        state=inbox.check(Context(Page(restore_failure=True)),self.root,force=True)
        self.assertEqual(state['state'],'ok')
        self.assertEqual(state['checked_folders'],['inbox','requests','hidden'])
        self.assertEqual(state['restore_error'],'TimeoutError')
        self.assertEqual(state['consecutive_failures'],0)
    def test_login_is_not_retried_or_hidden_by_restore_failure(self):
        page=Page(results={'':{**EMPTY,'gate':True}},restore_failure=True)
        state=inbox.check(Context(page),self.root,force=True)
        self.assertEqual(state['error_code'],'login_required')
        self.assertEqual(page.visits.count('https://www.threads.com/messages'),1)
        self.assertIn('Continue with Instagram',page.ready_script)
        self.assertEqual(state['restore_error'],'TimeoutError')
    def test_challenge_is_not_retried(self):
        page=Page(results={'':{**EMPTY,'challenge':True}})
        state=inbox.check(Context(page),self.root,force=True)
        self.assertEqual(state['error_code'],'challenge_required')
        self.assertEqual(page.visits.count('https://www.threads.com/messages'),1)
    def test_unknown_layout_is_never_empty_success(self):
        state=inbox.check(Context(Page(results={'':{**EMPTY,'empty':False}})),self.root,force=True)
        self.assertEqual(state['state'],'error')
        self.assertEqual(state['error_code'],'layout_unrecognized')
        self.assertEqual(state['checked_folders'],[])
        self.assertNotIn('visible_conversations',state)
    def test_partial_progress_survives_later_folder_failure(self):
        state=inbox.check(Context(Page(failures={'/hidden':2})),self.root,force=True)
        self.assertEqual(state['state'],'error')
        self.assertEqual(state['checked_folders'],['inbox','requests'])
        self.assertEqual(state['folder'],'hidden')
        self.assertNotIn('visible_conversations',state)
    def test_history_does_not_store_exception_body_and_survives_success(self):
        inbox.check(Context(Page(failures={'':2})),self.root,force=True)
        inbox.check(Context(Page()),self.root,force=True)
        text=(self.root/'inbox-diagnostics.jsonl').read_text()
        self.assertNotIn('secret',text)
        events=[json.loads(line) for line in text.splitlines()]
        self.assertTrue(any(e['state']=='error' for e in events))
        self.assertTrue(any(e['state']=='ok' for e in events))
        self.assertEqual(json.loads((self.root/'inbox-status.json').read_text())['state'],'ok')
    def test_failure_counter_resets_only_after_success(self):
        one=inbox.check(Context(Page(failures={'':2})),self.root,force=True)
        two=inbox.check(Context(Page(failures={'':2})),self.root,force=True)
        good=inbox.check(Context(Page()),self.root,force=True)
        self.assertEqual([one['consecutive_failures'],two['consecutive_failures'],good['consecutive_failures']],[1,2,0])
    def test_preview_is_hashed_and_outgoing_does_not_notify(self):
        row={'url':'https://www.threads.com/messages/123','sender':'Fixture','unread':True,'preview':'You: private outgoing text'}
        page=Page(results={'':{**EMPTY,'empty':False,'rows':[row]}})
        found=inbox.scan(page)
        self.assertNotIn('preview',found['items'][0])
        self.assertFalse(found['items'][0]['unread'])
        self.assertEqual(len(found['items'][0]['fingerprint']),64)
    def test_activity_has_bounded_deadline_and_returns_idle(self):
        written=[]
        original=inbox.activity
        def record(root,stage,deadline,state='running'):
            original(root,stage,deadline,state)
            written.append(json.loads((Path(root)/'activity.json').read_text()))
        with patch.object(inbox,'activity',side_effect=record):
            inbox.check(Context(Page()),self.root,force=True)
        self.assertTrue(any(e['stage']=='inbox_navigate_requests' for e in written))
        self.assertTrue(any(e['stage']=='inbox_restore' for e in written))
        self.assertTrue(all(e['deadline_at']-e['updated_at']<=180 for e in written))
        self.assertEqual(written[-1]['state'],'idle')
        self.assertEqual(written[-1]['campaign'],'inbox')
    def test_no_operation_exceeds_25_seconds(self):
        page=Page()
        inbox.check(Context(page),self.root,force=True)
        self.assertTrue(all(0<t<=25000 for t in page.timeouts))
    def test_expired_total_budget_prevents_more_navigation(self):
        page=Page()
        with patch.object(inbox.time,'monotonic',side_effect=[0,111]):
            with self.assertRaises(inbox.InboxReadError) as failure:inbox.scan(page)
        self.assertEqual(failure.exception.code,'deadline')
        self.assertEqual(page.visits,[])
    def test_existing_alert_delivery_is_not_replayed_on_read_retry(self):
        row={'url':'https://www.threads.com/messages/123','sender':'Fixture','unread':True,'preview':'Incoming fixture'}
        for _ in range(2):
            page=Page(failures={'/requests':1},results={'':{**EMPTY,'empty':False,'rows':[row]}})
            inbox.check(Context(page),self.root,force=True)
        monitor=inbox.Monitor(self.root,recover=False)
        try:self.assertEqual(monitor.db.execute("SELECT count(*) FROM outbox WHERE id LIKE 'dm:%'").fetchone()[0],1)
        finally:monitor.close()


if __name__=='__main__':unittest.main()
