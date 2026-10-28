"""Apply native subscription settings without replacing live tool credentials."""
import ast
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'data/iva-pilot/app'


def configure():
    env={}
    for line in (APP/'.env').read_text().splitlines():
        if '=' in line:
            k,v=line.split('=',1);env[k]=v
    env.update(MODEL_PROVIDER='codex',CODEX_MODEL='gpt-6-luna',
               HELPER_MODEL='gpt-6-luna',THINKING_EFFORT='low',
               CODEX_CONTEXT_WINDOW='240000',CODEX_HOME='/home/iva/.codex',
               IVA_SHARED_AUTH=str(ROOT/'local_finance/shared_auth.py'),
               IVA_AUTH_PYTHON=str(ROOT/'.venv/bin/python'),
               IVA_SERVICE_TIER='fast',
               NODE_OPTIONS='--import='+str(APP/'native-proxy.mjs'))
    (APP/'.env').write_text('\n'.join(f'{k}={v}' for k,v in env.items())+'\n')
    os.chmod(APP/'.env',0o600)
    (APP/'native-proxy.mjs').write_text(r"""
import {ProxyAgent,fetch as proxyFetch} from 'undici';
import {appendFileSync} from 'node:fs';
import {fastModeFetch} from '/opt/iva/iva_integration/fast-mode.mjs';
const direct=globalThis.fetch;
const proxy=new ProxyAgent('http://10.203.77.2:8888');
const routed=(input,init={})=>{
 const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
 return ['chatgpt.com','auth.openai.com'].includes(url.hostname)?proxyFetch(input,{...init,dispatcher:proxy}):direct(input,init);
};
globalThis.fetch=fastModeFetch(routed,{
 enabled:process.env.IVA_SERVICE_TIER==='fast',
 record:value=>appendFileSync('/opt/iva/data/fast-mode-audit.jsonl',JSON.stringify({at:new Date().toISOString(),...value})+'\n',{mode:0o600}),
});
""")
    tree=ast.parse((ROOT/'iva_integration/gateway.py').read_text())
    rules=[n.value.value for n in ast.walk(tree)
           if isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='instruction'
           and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str) and 'JSON ROUTER' not in n.value.value]
    (APP/'agent/instructions/99-pilot.md').write_text(
        '# Рабочая Ива\nТы личный ассистент владельца. Отвечай по-русски. Используй нативные инструменты. '
        'Подтверждай выполнение только по результату инструмента. Финансовые факты хранятся в реестре на VPS.\n'+''.join(rules))
    for name in ['agent/tools/family_finance.ts','agent/connections/family-finance.ts']:
        (APP/name).unlink(missing_ok=True)
    path=APP/'agent/lib/codex-auth.ts'
    text=path.read_text()
    if 'IVA_SHARED_AUTH' not in text:
        text='import { execFile as sharedExecFile } from "node:child_process";\n'+text
        text+='''
// One serialized credential owner shared with the CLI subagents.
function sharedSubscriptionToken(force = false): Promise<AccessToken> {
  return new Promise((resolve,reject)=>{
    sharedExecFile(process.env.IVA_AUTH_PYTHON!,
      [process.env.IVA_SHARED_AUTH!,...(force?["--force"]:[])],
      {timeout:480000,maxBuffer:65536},(error,stdout)=>{
        if(error){reject(new Error("Shared subscription authorization unavailable"));return;}
        try {
          const value=JSON.parse(stdout);
          if(!value.accessToken)throw new Error("Missing access token");
          resolve(value);
        } catch { reject(new Error("Invalid subscription authorization response")); }
      });
  });
}
'''
        text=text.replace('  const auth = readAuth(dataDir);\n  if (!auth?.access_token)',
                          '  if (process.env.IVA_SHARED_AUTH) return sharedSubscriptionToken();\n  const auth = readAuth(dataDir);\n  if (!auth?.access_token)',1)
        marker='export async function forceRefreshAccessToken('
        before,after=text.split(marker,1)
        after=after.replace('  const auth = readAuth(dataDir);','''  if (process.env.IVA_SHARED_AUTH) {
    const force = Date.now()-lastForcedRefreshAt >= FORCE_REFRESH_COOLDOWN_MS;
    if(force)lastForcedRefreshAt=Date.now();
    return sharedSubscriptionToken(force);
  }
  const auth = readAuth(dataDir);''',1)
        path.write_text(before+marker+after)
    (ROOT/'data/iva-provider.json').write_text(json.dumps({'provider':'codex','auth':'shared-cli-file','model':'gpt-6-luna','reasoning':'low','service_tier':'fast'}))


if __name__=='__main__':
    configure()
    print('native_configuration_applied')
