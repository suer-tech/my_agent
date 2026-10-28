import json,os,time,hashlib
from pathlib import Path
from local_finance.bot import load_env,Telegram
from local_finance.iva_telegram import EveBridge
from local_finance.assistant import Ledger
ROOT=Path('/opt/iva');load_env(ROOT/'.env')
me=Telegram(os.environ['TELEGRAM_BOT_TOKEN']).call('getMe',{})
print(json.dumps({'telegram_proxy_authenticated':True,'bot':me.get('username')}),flush=True)
folder=ROOT/'data/iva-pilot/diagnostics'/('server-'+str(time.time_ns()));folder.mkdir(parents=True)
answer=EveBridge(state_dir=folder,skip_daily=True).send('Диагностика переноса на сервер: через финансового субагента прочитай текущий остаток реестра и количество кредитов. Ничего не сохраняй и не меняй. Ответь коротко и обозначь, что это проверка.','diagnostic:server:'+folder.name)
l=Ledger(ROOT/'data/local-finance.sqlite3');ops=l.rows('SELECT * FROM operations ORDER BY id');l.db.close()
m=json.loads((ROOT/'data/migration-manifest.json').read_text());assert hashlib.sha256(json.dumps(ops,sort_keys=True,ensure_ascii=False).encode()).hexdigest()==m['operations_sha256']
result={'telegram_proxy_authenticated':True,'live_iva_finance_chain':True,'financial_operations_unchanged':True,'answer':answer,'diagnostic_dir':str(folder)}
(ROOT/'data/server-preflight.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True),flush=True)
