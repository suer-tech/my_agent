"""Prepare an isolated full Iva installation. Never starts a Telegram poller."""
import ast,json,os,secrets,shutil,sqlite3
from pathlib import Path
LIVE=Path('/opt/iva')
ROOT=LIVE/'data/native-e2e'
assert not ROOT.exists(), 'Reuse/inspect existing sandbox rather than overwriting it'
ROOT.mkdir(mode=0o700)
for name in ['local_finance','iva_integration','plugin','deploy','tests']:
    shutil.copytree(LIVE/name,ROOT/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
data=ROOT/'data';data.mkdir()
for name in ['iva-model.json','iva-agents.json','cartesia-settings.json','cartesia.env','eis-ca.pem']:
    shutil.copy2(LIVE/'data'/name,data/name)
    os.chmod(data/name,0o600)
for name in ['local-finance.sqlite3','tender-agent.sqlite3']:
    with sqlite3.connect(f'file:{LIVE}/data/{name}?mode=ro',uri=True) as source,sqlite3.connect(data/name) as target:
        source.backup(target)
with sqlite3.connect(data/'local-finance.sqlite3') as db:
    db.execute("UPDATE local_updates SET state='done' WHERE state!='done'")
shutil.copytree(LIVE/'data/documents',data/'documents')
# Read-only large speech binaries/model can be shared; all generated audio stays here.
(data/'voice').symlink_to(LIVE/'data/voice',target_is_directory=True)
pilot=data/'iva-pilot';pilot.mkdir()
app=pilot/'app'
shutil.copytree(LIVE/'data/iva-pilot/app',app,
    ignore=shutil.ignore_patterns('.eve','.output','.memory.lock','*.log'))
# New Eve session store, preserving only the copied vault.
if (app/'data').exists():shutil.rmtree(app/'data')
(app/'data').mkdir(mode=0o700)
control={'gateway_port':18825,'iva_port':18823,
         'agent_token':secrets.token_hex(32),'owner_token':secrets.token_hex(32),'iva_bearer':secrets.token_hex(32)}
(pilot/'control.json').write_text(json.dumps(control));os.chmod(pilot/'control.json',0o600)
env={}
for line in (app/'.env').read_text().splitlines():
    if '=' in line:
        k,v=line.split('=',1)
        if not any(s in k.upper() for s in ['TELEGRAM','BOT_TOKEN']):env[k]=v
for k,v in list(env.items()):env[k]=v.replace('/opt/iva/',str(ROOT)+'/')
# CLI remains the existing executable and uses the existing supported login.
env['FINANCE_CODEX_EXE']='/usr/local/bin/iva-codex'
env['CODEX_HOME']='/home/iva/.codex'
env.update(MODEL_PROVIDER='codex',CODEX_MODEL='gpt-6-luna',HELPER_MODEL='gpt-6-luna',
 THINKING_EFFORT='low',CODEX_CONTEXT_WINDOW='240000',
 ASSISTANT_DATA_DIR=str(app/'data'),ASSISTANT_VAULT_DIR=str(app/'vault'),
 PORT='18823',IVA_PORT='18823',ASSISTANT_BEARER=control['iva_bearer'],
 PILOT_GATEWAY_URL='http://127.0.0.1:18825',PILOT_AGENT_TOKEN=control['agent_token'],
 CUSTOM_BASE_URL='http://127.0.0.1:18825/v1',
 NODE_OPTIONS='--import='+str(app/'native-proxy.mjs'))
(app/'.env').write_text('\n'.join(f'{k}={v}' for k,v in env.items())+'\n');os.chmod(app/'.env',0o600)
(data/'iva-runtime.json').write_text(json.dumps({'mode':'telegram','finance_database':str(data/'local-finance.sqlite3')}))
(data/'iva-telegram').mkdir();(data/'iva-telegram/session.json').write_text('{}')
# Only OpenAI model/auth requests use VPN. Everything else keeps its host route.
(app/'native-proxy.mjs').write_text("""
import {ProxyAgent} from 'undici';
const direct=globalThis.fetch;
const proxy=new ProxyAgent('http://10.203.77.2:8888');
globalThis.fetch=(input,init={})=>{
 const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
 return direct(input,['chatgpt.com','auth.openai.com'].includes(url.hostname)?{...init,dispatcher:proxy}:init);
};
""")
# No refresh credential shared with the running CLI; use its current access token.
auth=json.loads((Path('/home/iva/.codex/auth.json')).read_text())['tokens']
(app/'data/codex-auth.json').write_text(json.dumps({'access_token':auth['access_token'],'accountId':auth.get('account_id'),'planType':None}))
os.chmod(app/'data/codex-auth.json',0o600)
tree=ast.parse((LIVE/'iva_integration/gateway.py').read_text())
rules=[]
for n in ast.walk(tree):
    if isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='instruction' and isinstance(n.value,ast.Constant):
        if isinstance(n.value.value,str) and 'JSON ROUTER' not in n.value.value:rules.append(n.value.value)
(app/'agent/instructions/99-pilot.md').write_text(
 '# Deployment rules\nIva is the owner personal assistant. Use native tools, not CLI commands. '
 'This is an isolated acceptance environment with copied data. All writes belong to this copy. '
 'Respond in Russian. Never claim success before tool result.\n'+''.join(rules))
# The model adapter formerly filtered this legacy tool. Remove it from the clone.
(app/'agent/tools/family_finance.ts').unlink(missing_ok=True)
(app/'agent/connections/family-finance.ts').unlink(missing_ok=True)
# Copied connection credentials must not grant personal accounts to the sandbox.
connections=app/'agent/connections'
if connections.exists():
    for f in connections.glob('*.ts'):
        if not f.name.endswith('.test.ts'):f.rename(f.with_suffix('.disabled'))
manifest={'root':str(ROOT),'model':'gpt-6-luna','ports':[18823,18825],
 'financial_snapshot':str(data/'local-finance.sqlite3'),'telegram_poller':False,'transport':'captured in test harness',
 'native_refresh_token_copied':False}
(data/'isolation.json').write_text(json.dumps(manifest,indent=2))
print('isolated_installation_prepared')
