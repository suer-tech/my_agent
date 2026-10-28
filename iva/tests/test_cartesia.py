from pathlib import Path
import io
import json
import struct
import sys
import tempfile
import unittest
import urllib.error
import wave
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.cartesia import Cartesia,CartesiaError


def wav():
    out=io.BytesIO()
    with wave.open(out,'wb') as writer:
        writer.setnchannels(1);writer.setsampwidth(2);writer.setframerate(44100);writer.writeframes(b'\x01\x02'*44100)
    return out.getvalue()


class Response:
    def __init__(self,content):self.content=content
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return self.content


class CartesiaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'data').mkdir()
        (self.root/'data/cartesia.env').write_text('CARTESIA_API_KEY=synthetic-key-never-print\n')
        (self.root/'data/cartesia-settings.json').write_text(json.dumps({'enabled':True,'model_id':'test-model','voice_id':'test-voice'}))
    def tearDown(self):self.temp.cleanup()

    def test_real_payload_and_cache_avoid_duplicate_charge(self):
        with patch('local_finance.cartesia.urllib.request.urlopen',return_value=Response(wav())) as network:
            engine=Cartesia(self.root);first=engine.synthesize_wav('Привет');second=engine.synthesize_wav('Привет')
            self.assertEqual(first,second);self.assertEqual(network.call_count,1)
            request=network.call_args.args[0];payload=json.loads(request.data)
            self.assertEqual(payload['language'],'ru');self.assertEqual(payload['voice'],'test-voice')
            self.assertNotIn('synthetic-key',request.full_url);self.assertNotIn('synthetic-key',request.data.decode())

    def test_streaming_unknown_frame_length_uses_received_frames(self):
        data=bytearray(wav());data[4:8]=struct.pack('<I',0xffffffff);data[40:44]=struct.pack('<I',0xffffffff)
        Cartesia.validate(bytes(data))

    def test_http_failure_does_not_leak_or_retry(self):
        error=urllib.error.HTTPError('https://example.invalid/synthetic-key-never-print',401,'synthetic-key-never-print',{},None)
        with patch('local_finance.cartesia.urllib.request.urlopen',side_effect=error) as network:
            with self.assertRaises(CartesiaError) as caught:Cartesia(self.root).synthesize_wav('Привет')
            self.assertNotIn('synthetic-key',str(caught.exception));self.assertEqual(network.call_count,1)

    def test_invalid_audio_is_not_cached(self):
        with patch('local_finance.cartesia.urllib.request.urlopen',return_value=Response(b'not audio')):
            with self.assertRaises(CartesiaError):Cartesia(self.root).synthesize_wav('Привет')
        self.assertEqual(list((self.root/'data/tts-cache/cartesia').glob('*.wav')),[])

    def test_missing_key_fails_without_network(self):
        (self.root/'data/cartesia.env').unlink()
        with patch('local_finance.cartesia.urllib.request.urlopen') as network:
            with self.assertRaises(CartesiaError):Cartesia(self.root).synthesize_wav('Привет')
            network.assert_not_called()


if __name__=='__main__':unittest.main()
