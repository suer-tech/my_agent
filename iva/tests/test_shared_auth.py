import base64
import json
import time
from unittest.mock import patch
import unittest
from local_finance.shared_auth import token


def login(tmp_path, expiry):
    jwt='x.'+base64.urlsafe_b64encode(json.dumps({'exp':expiry}).encode()).decode().rstrip('=')+'.x'
    value={'tokens':{'access_token':jwt,'refresh_token':'old','account_id':'test-account'}}
    (tmp_path/'auth.json').write_text(json.dumps(value))
    return value


def test_fresh_shared_login_needs_no_network(tmp_path):
    value=login(tmp_path,time.time()+3600)
    with patch('urllib.request.build_opener',side_effect=AssertionError('network')):
        assert token(tmp_path)['accessToken']==value['tokens']['access_token']


def test_refresh_persists_rotated_credential_for_cli(tmp_path):
    login(tmp_path,0)
    import io
    class Client:
        def open(self,*a,**kw):
            return io.BytesIO(json.dumps({'access_token':'new-access','refresh_token':'new-refresh'}).encode())
    with patch('urllib.request.build_opener',return_value=Client()):
        assert token(tmp_path)['accessToken']=='new-access'
    saved=json.loads((tmp_path/'auth.json').read_text())
    assert saved['tokens']['refresh_token']=='new-refresh'
    assert saved['tokens']['account_id']=='test-account'


def test_failed_refresh_preserves_login(tmp_path):
    value=login(tmp_path,0)
    with patch('urllib.request.build_opener',side_effect=RuntimeError('offline')):
        with unittest.TestCase().assertRaises(RuntimeError):
            token(tmp_path)
    assert json.loads((tmp_path/'auth.json').read_text())==value


if __name__=='__main__':
    import tempfile
    from pathlib import Path
    for check in (test_fresh_shared_login_needs_no_network,
                  test_refresh_persists_rotated_credential_for_cli,
                  test_failed_refresh_preserves_login):
        with tempfile.TemporaryDirectory() as tmp:
            check(Path(tmp))
    print('3 shared authorization checks passed')
