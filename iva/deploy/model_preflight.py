"""Verify actual subscribed Codex CLI access on the target, without finance writes."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.codex_cli import Codex

def main():
    results=[]
    schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
    for role in [None,'finance']:
        cli=Codex(role=role,timeout=120)
        result=cli.structured('Проверка подключения. Верни {"ok":true}. Инструменты не нужны.',schema)
        if result!={'ok':True}:raise RuntimeError('Unexpected model response')
        results.append({'role':role or 'orchestrator','model':cli.model,'effort':cli.reasoning_effort,'ok':True})
    (ROOT/'data/preflight-models.json').write_text(json.dumps(results),encoding='utf-8')
    print(json.dumps(results))

if __name__=='__main__':main()
