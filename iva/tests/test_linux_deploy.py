import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from deploy.prepare_bundle import relocate
from local_finance.voice import Voice


class LinuxDeploymentTests(unittest.TestCase):
    def test_relocation_keeps_sources_and_substrings_outside_root(self):
        value={'path':r'E:\Projects\main_agent\data\documents\one.pdf','refs':['telegram:1:2:0'],
               'other':r'E:\Projects\main_agent-old\file','text':r'old E:\Projects\main_agent\file'}
        result=relocate(value,r'E:\Projects\main_agent')
        self.assertEqual(result['path'],'/opt/iva/data/documents/one.pdf')
        self.assertEqual(result['refs'],value['refs']);self.assertEqual(result['other'],value['other']);self.assertEqual(result['text'],value['text'])

    def test_linux_voice_discovers_system_ffmpeg_and_built_whisper(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);binary=root/'data/voice/whisper/build/bin/whisper-cli';binary.parent.mkdir(parents=True);binary.touch()
            (root/'data/voice/ggml-base.bin').touch()
            with patch('local_finance.voice.ROOT',root),patch('local_finance.voice.os',SimpleNamespace(name='posix')),patch('local_finance.voice.shutil.which',side_effect=lambda name:'/usr/bin/ffmpeg' if name=='ffmpeg' else None):
                voice=Voice();self.assertTrue(voice.ready);self.assertEqual(voice.whisper,binary)


if __name__=='__main__':unittest.main()
