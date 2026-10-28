import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from local_finance.opencode_service import run,_provider_error_metadata,invocation_lock
from local_finance.opencode_cli import OpenCodeError


class Reply:
    def __init__(self,value):self.value=value
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.value).encode()


class Service(unittest.TestCase):
    def execute(self,handler,timeout_seconds=12):
        with tempfile.TemporaryDirectory() as tmp:
            password=Path(tmp)/'password';password.write_text('test-only-secret')
            (Path(tmp)/'inference.lock').touch()
            cfg={'password_file':str(password),'state_root':tmp,'server_url':'http://127.0.0.1:18726','timeout_seconds':timeout_seconds}
            with patch('local_finance.opencode_service.urllib.request.urlopen',side_effect=handler):return run(cfg,'test prompt','unique-test')
    def ready(self):
        return {'all':[{'id':'opencode','models':{'deepseek-v4.1-flash-free':{}}}],'connected':['opencode']}
    def answer(self):
        return [{'info':{'role':'assistant','modelID':'deepseek-v4.1-flash-free','providerID':'opencode','finish':'stop','time':{'completed':1}},'parts':[{'type':'text','text':'{"route":"reply","reply":"ok"}'}]}]
    def test_success_acceptance_has_larger_timeout(self):
        timeouts=[]
        def api(req,timeout):
            path=req.full_url.split('?')[0];timeouts.append((req.get_method(),timeout))
            if path.endswith('/provider'):return Reply(self.ready())
            if path.endswith('/session'):return Reply({'id':'test'})
            if path.endswith('/message'):return Reply(self.answer())
            return Reply([])
        result,metrics=self.execute(api)
        self.assertEqual(result['reply'],'ok');self.assertEqual(metrics['actual_model'],'opencode/deepseek-v4.1-flash-free')
        self.assertLessEqual(timeouts[0][1],3)
        self.assertGreater(timeouts[1][1],3)
    def test_uncertain_acceptance_reads_session_without_resubmitting(self):
        submits=[]
        def api(req,timeout):
            path=req.full_url.split('?')[0]
            if path.endswith('/provider'):return Reply(self.ready())
            if path.endswith('/session'):return Reply({'id':'test'})
            if path.endswith('/prompt_async'):submits.append(req.data);raise TimeoutError('uncertain acceptance')
            if path.endswith('/message'):return Reply(self.answer())
            return Reply([])
        result,_=self.execute(api);self.assertEqual(result['reply'],'ok');self.assertEqual(len(submits),1)
    def test_creation_timeout_not_repeated_and_safe_diagnostics(self):
        calls=[]
        def api(req,timeout):
            calls.append(req)
            if req.full_url.split('?')[0].endswith('/provider'):return Reply(self.ready())
            raise TimeoutError('must-not-log-test-secret')
        with self.assertRaises(OpenCodeError) as caught:self.execute(api)
        self.assertEqual(len(calls),2);self.assertEqual(caught.exception.diagnostics['stage'],'create_session')
        self.assertFalse(caught.exception.diagnostics['cleanup_attempted'])
        self.assertNotIn('secret',str(caught.exception));self.assertNotIn('secret',json.dumps(caught.exception.diagnostics))
    def test_provider_failure_is_identified_not_a_launch_failure(self):
        def api(req,timeout):
            path=req.full_url.split('?')[0]
            if path.endswith('/provider'):return Reply(self.ready())
            if path.endswith('/session'):return Reply({'id':'test'})
            if path.endswith('/message'):return Reply([{'info':{'role':'assistant','error':{'name':'APIError','data':{
                'statusCode':429,'isRetryable':True,'message':'private provider response','responseHeaders':{'Authorization':'secret'}}}}}])
            if path.endswith('/abort'):return Reply(True)
            return Reply([])
        with self.assertRaises(OpenCodeError) as caught:self.execute(api)
        self.assertEqual(caught.exception.diagnostics['stage'],'provider_error')
        self.assertEqual(caught.exception.diagnostics['category'],'provider_rejected')
        self.assertEqual(caught.exception.diagnostics['provider_http_status'],429)
        self.assertTrue(caught.exception.diagnostics['provider_retryable'])
        self.assertTrue(caught.exception.diagnostics['cleanup_attempted'])
        self.assertTrue(caught.exception.diagnostics['cleanup_confirmed'])
        self.assertNotIn('secret',json.dumps(caught.exception.diagnostics))
        self.assertNotIn('private provider response',json.dumps(caught.exception.diagnostics))
    def test_native_tool_is_rejected(self):
        def api(req,timeout):
            path=req.full_url.split('?')[0]
            if path.endswith('/provider'):return Reply(self.ready())
            if path.endswith('/session'):return Reply({'id':'test'})
            if path.endswith('/permission'):return Reply([{'id':'permission','sessionID':'test'}])
            return Reply([])
        with self.assertRaises(OpenCodeError):self.execute(api)

    def test_preflight_503_falls_back_without_creating_or_submitting_a_session(self):
        calls=[]
        def api(req,timeout):
            calls.append((req.full_url,req.get_method(),timeout))
            raise urllib.error.HTTPError(req.full_url,503,'private provider body',{'Authorization':'secret'},None)
        with self.assertRaises(OpenCodeError) as caught:self.execute(api)
        diagnostic=caught.exception.diagnostics
        self.assertEqual(len(calls),1)
        self.assertIn('/provider?',calls[0][0])
        self.assertEqual(calls[0][1],'GET')
        self.assertLessEqual(calls[0][2],3)
        self.assertEqual(diagnostic['stage'],'provider_preflight')
        self.assertEqual(diagnostic['category'],'provider_unavailable')
        self.assertEqual(diagnostic['http_status'],503)
        self.assertFalse(diagnostic['cleanup_attempted'])
        self.assertNotIn('private',json.dumps(diagnostic))
        self.assertNotIn('secret',json.dumps(diagnostic))

    def test_preflight_requires_connected_provider_and_exact_model(self):
        variants=[None,[],{'all':[],'connected':['opencode']},
                  {**self.ready(),'connected':[]},
                  {'all':[{'id':'opencode','models':{'other-model':{}}}],'connected':['opencode']},
                  {'all':[{'id':'other-provider','models':{'deepseek-v4.1-flash-free':{}}}],'connected':['opencode']}]
        for value in variants:
            with self.subTest(value=value):
                calls=[]
                def api(req,timeout):calls.append(req);return Reply(value)
                with self.assertRaises(OpenCodeError) as caught:self.execute(api)
                self.assertEqual(len(calls),1)
                self.assertEqual(caught.exception.diagnostics['category'],'provider_unavailable')
                self.assertFalse(caught.exception.diagnostics['cleanup_attempted'])

    def test_preflight_timeout_is_not_retried(self):
        calls=[]
        def api(req,timeout):calls.append(timeout);raise TimeoutError('private timeout')
        with self.assertRaises(OpenCodeError) as caught:self.execute(api)
        self.assertEqual(len(calls),1)
        self.assertLessEqual(calls[0],3)
        self.assertEqual(caught.exception.diagnostics['stage'],'provider_preflight')
        self.assertEqual(caught.exception.diagnostics['kind'],'TimeoutError')

    def test_preflight_uses_the_same_total_deadline(self):
        clock=[0.0];calls=[]
        def api(req,timeout):
            path=req.full_url.split('?')[0];calls.append((path,timeout))
            if path.endswith('/provider'):
                clock[0]=1.75
                return Reply(self.ready())
            if path.endswith('/session'):return Reply({'id':'test'})
            if path.endswith('/message'):return Reply(self.answer())
            return Reply([])
        with patch('local_finance.opencode_service.time.monotonic',side_effect=lambda:clock[0]):
            result,_=self.execute(api,timeout_seconds=2)
        self.assertEqual(result['reply'],'ok')
        self.assertEqual(calls[0][1],2)
        self.assertEqual(calls[1][1],0.25)

    def test_provider_metadata_allowlist_and_unconfirmed_cleanup(self):
        self.assertEqual(_provider_error_metadata({'name':['secret'],'data':{'statusCode':True,'isRetryable':'secret'}}),
                         {'provider_error_name':'OtherProviderError'})
        self.assertEqual(_provider_error_metadata({'name':'private-provider-name','data':{'statusCode':999}}),
                         {'provider_error_name':'OtherProviderError'})
        def api(req,timeout):
            path=req.full_url.split('?')[0]
            if path.endswith('/provider'):return Reply(self.ready())
            if path.endswith('/session'):return Reply({'id':'test'})
            if path.endswith('/message'):return Reply([{'info':{'role':'assistant','error':{'name':'ProviderAuthError'}}}])
            if path.endswith('/abort'):raise TimeoutError('cleanup secret')
            return Reply([])
        with self.assertRaises(OpenCodeError) as caught:self.execute(api)
        diagnostic=caught.exception.diagnostics
        self.assertEqual(diagnostic['provider_error_name'],'ProviderAuthError')
        self.assertTrue(diagnostic['cleanup_attempted'])
        self.assertFalse(diagnostic['cleanup_confirmed'])
        self.assertNotIn('cleanup secret',json.dumps(diagnostic))

    @unittest.skipUnless(os.name=='posix','server invocation locks use Unix flock')
    def test_exclusive_maintenance_lock_prevents_any_http_request(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            password=Path(tmp)/'password';password.write_text('test-only-secret')
            lock=Path(tmp)/'inference.lock';lock.touch()
            cfg={'password_file':str(password),'state_root':tmp,'server_url':'http://127.0.0.1:18726'}
            with lock.open('rb') as stream,patch('local_finance.opencode_service.urllib.request.urlopen') as api:
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(OpenCodeError) as caught:run(cfg,'test prompt','unique-test')
                self.assertEqual(caught.exception.diagnostics['category'],'maintenance_busy')
                api.assert_not_called()

    @unittest.skipUnless(os.name=='posix','server invocation locks use Unix flock')
    def test_invocation_holds_shared_lock_through_abort_cleanup_and_releases_it(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            password=Path(tmp)/'password';password.write_text('test-only-secret')
            lock=Path(tmp)/'inference.lock';lock.touch()
            cfg={'password_file':str(password),'state_root':tmp,'server_url':'http://127.0.0.1:18726'}
            checked=[]
            def api(req,timeout):
                path=req.full_url.split('?')[0]
                with lock.open('rb') as exclusive:
                    with self.assertRaises(BlockingIOError):fcntl.flock(exclusive.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                checked.append(path)
                if path.endswith('/provider'):return Reply(self.ready())
                if path.endswith('/session'):return Reply({'id':'test'})
                if path.endswith('/message'):return Reply([{'info':{'role':'assistant','error':{'name':'APIError'}}}])
                if path.endswith('/abort'):return Reply(True)
                return Reply([])
            with patch('local_finance.opencode_service.urllib.request.urlopen',side_effect=api):
                with self.assertRaises(OpenCodeError) as caught:run(cfg,'test prompt','unique-test')
            self.assertTrue(any(path.endswith('/abort') for path in checked))
            self.assertTrue(caught.exception.diagnostics['cleanup_confirmed'])
            with lock.open('rb') as exclusive:fcntl.flock(exclusive.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)

    @unittest.skipUnless(os.name=='posix','server invocation locks use Unix flock')
    def test_missing_invocation_lock_does_not_run_uncoordinated(self):
        with tempfile.TemporaryDirectory() as tmp,patch('local_finance.opencode_service.urllib.request.urlopen') as api:
            cfg={'password_file':str(Path(tmp)/'password')}
            with self.assertRaises(OpenCodeError) as caught:run(cfg,'test prompt','unique-test')
            self.assertEqual(caught.exception.diagnostics['category'],'lock_unavailable')
            api.assert_not_called()

    @unittest.skipUnless(os.name=='posix','server invocation locks use Unix flock')
    def test_symlink_invocation_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch('local_finance.opencode_service.urllib.request.urlopen') as api:
            root=Path(tmp);target=root/'other.lock';target.touch()
            (root/'inference.lock').symlink_to(target)
            cfg={'password_file':str(root/'password')}
            with self.assertRaises(OpenCodeError) as caught:run(cfg,'test prompt','unique-test')
            self.assertEqual(caught.exception.diagnostics['category'],'lock_unavailable')
            api.assert_not_called()


if __name__=='__main__':unittest.main()
