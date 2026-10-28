"""Verify staged transport and actual TTS without polling or Telegram sends."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT=Path('/opt/iva')
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release',required=True,type=Path);args=parser.parse_args()
    release=args.release.resolve();source=release/'source'
    from local_finance.assistant import Ledger
    for name in ('voice','bot','iva_telegram'):
        spec=importlib.util.spec_from_file_location('local_finance.'+name,source/'local_finance'/f'{name}.py')
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        module.ROOT=ROOT
    tests=release/'test-suite';shutil.copytree(ROOT/'tests',tests,dirs_exist_ok=True)
    for name in ('test_iva_telegram.py','test_reply_buttons.py'):shutil.copy2(source/'tests'/name,tests/name)
    sys.path.insert(0,str(tests));suite=unittest.TestSuite()
    paths=sorted(tests.glob('test_*.py'),key=lambda p:(p.name!='test_iva_telegram.py',p.name))
    for path in paths:
        spec=importlib.util.spec_from_file_location(path.stem,path)
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
        module.__file__=str(ROOT/'tests'/path.name)
        spec.loader.exec_module(module);suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():raise RuntimeError('Staged tests failed')
    from local_finance.voice import Voice
    engine=Voice();assert engine.ready,'Voice runtime unavailable'
    audio=engine.synthesize('Голосовой режим готов. Буду отвечать так, как вы выбрали.')
    assert audio.startswith(b'OggS') and b'OpusHead' in audio[:512]
    (release/'tts-smoke.ogg').write_bytes(audio)
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(release/'tts-smoke.ogg')],capture_output=True,text=True,check=True)
    duration=float(json.loads(probe.stdout)['format']['duration']);assert duration>1
    report={'passed':True,'tests':result.testsRun,'skipped':len(result.skipped),'voice_ready':True,'real_tts_opus_bytes':len(audio),'duration_seconds':duration,'telegram_messages_sent':0}
    (release/'staged-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report))


if __name__=='__main__':main()
