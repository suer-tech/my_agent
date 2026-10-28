import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from local_finance.bot import Telegram

class TelegramProxyTests(unittest.TestCase):
    def test_proxy_applies_to_telegram_without_installing_global_opener(self):
        opener=SimpleNamespace(open=Mock(side_effect=lambda *a,**kw:io.BytesIO(json.dumps({'ok':True,'result':{'id':1}}).encode())))
        with patch.dict(os.environ,{'TELEGRAM_HTTPS_PROXY':'http://10.203.77.2:8888'}),patch('urllib.request.build_opener',return_value=opener),patch('urllib.request.urlopen') as direct,patch('urllib.request.install_opener') as install:
            telegram=Telegram('fake-token')
            self.assertEqual(telegram.call('getMe',{}),{'id':1})
            self.assertEqual(opener.open.call_count,1);direct.assert_not_called();install.assert_not_called()

    def test_voice_and_document_downloads_use_the_same_private_proxy(self):
        opener=SimpleNamespace(open=Mock(side_effect=lambda *a,**kw:io.BytesIO(b'attachment-content')))
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'TELEGRAM_HTTPS_PROXY':'http://10.203.77.2:8888'}),patch('urllib.request.build_opener',return_value=opener),patch('urllib.request.urlopen') as direct:
            telegram=Telegram('fake-token');telegram.call=Mock(return_value={'file_path':'files/test.bin','file_size':18})
            for index,method in enumerate([telegram.download_voice,telegram.download_attachment]):
                path=Path(d)/str(index);method({'file_id':'fixture'},path)
                self.assertEqual(path.read_bytes(),b'attachment-content')
            self.assertEqual(opener.open.call_count,2);direct.assert_not_called()

if __name__=='__main__':unittest.main()
