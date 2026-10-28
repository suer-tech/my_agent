"""Carry current gateway business instructions into an isolated native-provider probe."""
import ast,json,os,sys,subprocess,tempfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from local_finance.codex_cli import Codex
app=ROOT/'data/iva-pilot/app'
# Extract literal business policies without altering the production gateway.
tree=ast.parse((ROOT/'iva_integration/gateway.py').read_text(encoding='utf-8'))
policies=[]
for node in ast.walk(tree):
    if isinstance(node,ast.AugAssign) and isinstance(node.target,ast.Name) and node.target.id=='instruction' and isinstance(node.value,ast.Constant):
        if isinstance(node.value.value,str) and 'JSON ROUTER' not in node.value.value:
            policies.append(node.value.value)
system='Ты Ива, личная помощница. Сегодня 20 октября 2026, часовой пояс Asia/Yekaterinburg. Пользователь прямо поручает действия. Отвечай по-русски. Не заявляй об успехе до результата инструмента.\n'+''.join(policies)
with tempfile.TemporaryDirectory(prefix='iva-native-compat-') as d:
    p=Path(d);os.chmod(p,0o700)
    auth=json.loads((Codex().original_home/'auth.json').read_text())['tokens']
    token=p/'codex-auth.json'
    token.write_text(json.dumps({'access_token':auth['access_token'],'accountId':auth.get('account_id'),'planType':None}))
    os.chmod(token,0o600)
    inp=p/'input.json';inp.write_text(json.dumps({'system':system},ensure_ascii=False))
    script=app/'diagnostic-native-compatibility.mjs'
    shutil.copyfile(ROOT/'deploy/verify_native_compatibility.mjs',script)
    native_env=dict(os.environ,MODEL_PROVIDER='codex',CODEX_MODEL='gpt-6-luna',THINKING_EFFORT='low',
      ASSISTANT_DATA_DIR=d,NODE_USE_ENV_PROXY='1',HTTPS_PROXY='http://10.203.77.2:8888',
      HTTP_PROXY='http://10.203.77.2:8888',NO_PROXY='localhost,127.0.0.1')
    try:
        r=subprocess.run(['/usr/local/bin/node',str(script),str(inp),str(ROOT/'data/native-compatibility.json')],
          cwd=app,env=native_env,capture_output=True,text=True,timeout=720)
        print('native_runner_exit',r.returncode)
        if r.returncode:print(r.stderr[-1500:])
    finally:script.unlink(missing_ok=True)
