import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from local_finance.codex_cli import Codex,CliError


class FastModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'data').mkdir()
        self.runtime={'model':'gpt-6-luna','reasoning_effort':'low','cli_context_window':272000,'cli_auto_compact_token_limit':240000,'iva_context_window':240000,'service_tier':'fast'}
        self.write()
        self.patches=[patch('local_finance.codex_cli.__file__',str(self.root/'local_finance/codex_cli.py')),patch('local_finance.codex_cli.executable',return_value='codex'),patch.dict('os.environ',{'CODEX_HOME':str(self.root/'home')})]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def write(self):
        (self.root/'data/iva-model.json').write_text(json.dumps(self.runtime))
        (self.root/'data/iva-agents.json').write_text(json.dumps({'finance':{'model':'gpt-6.1-sol','reasoning_effort':'medium','service_tier':'fast'}}))

    def test_main_and_specialist_fast_preserve_model_and_reasoning(self):
        for role,model,effort in [(None,'gpt-6-luna','low'),('finance','gpt-6.1-sol','medium')]:
            command=Codex(role=role).command(self.tmp.name,'schema.json','out.json')
            self.assertEqual(command[command.index('--model')+1],model)
            self.assertIn('model_reasoning_effort="'+effort+'"',command)
            self.assertIn('service_tier="fast"',command)
            self.assertIn('features.fast_mode=true',command)

    def test_reload_can_disable_fast_without_reconstructing_client(self):
        cli=Codex();self.assertIn('service_tier="fast"',cli.command('work','schema','output'))
        self.runtime['service_tier']=None;self.write()
        self.assertNotIn('service_tier="fast"',cli.command('work','schema','output'))

    def test_invalid_service_tier_is_not_silently_enabled(self):
        self.runtime['service_tier']='ultrafast';self.write()
        with self.assertRaises(CliError):Codex()

    def test_generated_native_proxy_is_valid_javascript(self):
        source=Path(__file__).resolve().parents[1]/'deploy/configure_native.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        proxy=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'fastModeFetch(routed' in n.value)
        result=subprocess.run(['node','--check','--input-type=module'],input=proxy.encode(),capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode())


if __name__=='__main__':unittest.main()
