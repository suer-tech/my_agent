import base64
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from local_finance.shared_auth import token


class SharedAuthLatencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)

    def tearDown(self):self.tmp.cleanup()

    def save(self,expiry,account='fixture'):
        access='x.'+base64.urlsafe_b64encode(json.dumps({'exp':expiry}).encode()).decode().rstrip('=')+'.x'
        value={'tokens':{'access_token':access,'refresh_token':'fixture-refresh','account_id':account}}
        (self.home/'auth.json').write_text(json.dumps(value));return value

    def test_valid_token_does_not_wait_for_cli_lock_or_touch_network(self):
        value=self.save(time.time()+3600)
        with patch('local_finance.shared_auth.login_lock',side_effect=AssertionError('would wait for long CLI call')),patch('urllib.request.build_opener',side_effect=AssertionError('network')):
            self.assertEqual(token(self.home)['accessToken'],value['tokens']['access_token'])

    def test_expiring_token_is_rechecked_after_acquiring_refresh_lock(self):
        self.save(0)
        @contextmanager
        def rotated_by_cli(home):
            self.save(time.time()+3600,account='rotated');yield
        with patch('local_finance.shared_auth.login_lock',rotated_by_cli),patch('urllib.request.build_opener',side_effect=AssertionError('unnecessary refresh')):
            self.assertEqual(token(self.home)['accountId'],'rotated')

    def test_forced_refresh_keeps_lock_until_rotated_login_is_saved(self):
        self.save(time.time()+3600);held=[]
        @contextmanager
        def lock(home):
            held.append(True)
            try:yield
            finally:
                self.assertEqual(json.loads((self.home/'auth.json').read_text())['tokens']['refresh_token'],'rotated')
                held.pop()
        class Client:
            def open(inner,*args,**kwargs):
                self.assertTrue(held)
                return io.BytesIO(json.dumps({'access_token':'new','refresh_token':'rotated'}).encode())
        with patch('local_finance.shared_auth.login_lock',lock),patch('urllib.request.build_opener',return_value=Client()):
            self.assertEqual(token(self.home,force=True)['accessToken'],'new')
        self.assertFalse(held)

    def test_refresh_failure_preserves_login(self):
        before=self.save(0)
        with patch('urllib.request.build_opener',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):token(self.home)
        self.assertEqual(json.loads((self.home/'auth.json').read_text()),before)


if __name__=='__main__':unittest.main()
