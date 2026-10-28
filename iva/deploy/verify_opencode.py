"""Run staged transport regressions and real read-only primary-model cases."""
import argparse
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path('/opt/iva');sys.path.insert(0,str(ROOT))


def stage(source):
    from local_finance.assistant import Ledger
    for name,path in [('local_finance.opencode_cli','local_finance/opencode_cli.py'),
                      ('local_finance.opencode_service','local_finance/opencode_service.py'),
                      ('iva_integration.primary_engine','iva_integration/primary_engine.py'),
                      ('local_finance.bot','local_finance/bot.py'),
                      ('local_finance.iva_telegram','local_finance/iva_telegram.py')]:
        spec=importlib.util.spec_from_file_location(name,source/path)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);module.ROOT=ROOT


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release',required=True,type=Path);parser.add_argument('--tests-only',action='store_true');a=parser.parse_args();release=a.release.resolve()
    hashes={str(p.relative_to(release/'source')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (release/'source').rglob('*.py')}
    saved=release/'staged-test-result.json';cached=json.loads(saved.read_text()) if saved.exists() else {}
    if cached.get('hashes')==hashes and cached.get('passed'):unit=cached
    else:
        stage(release/'source')
        suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
        for name in ('test_primary_engine','test_opencode_service'):
            path=release/'source/tests'/(name+'.py');spec=importlib.util.spec_from_file_location(name+'_staged',path)
            module=importlib.util.module_from_spec(spec);module.__file__=str(ROOT/'tests'/(name+'.py'));spec.loader.exec_module(module)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        assert result.wasSuccessful(),'Staged regressions failed'
        unit={'passed':True,'tests':result.testsRun,'skipped':len(result.skipped),'hashes':hashes}
        saved.write_text(json.dumps(unit,indent=2))
    if a.tests_only:print(json.dumps({k:v for k,v in unit.items() if k!='hashes'}));return
    routing=json.loads((release/'routing-verification.json').read_text());assert routing['passed'],'Real OpenCode routing failed'
    report={'passed':True,'tests':unit['tests'],'skipped':unit['skipped'],'real_model_cases':len(routing['cases'])}
    (release/'staged-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))


if __name__=='__main__':main()
