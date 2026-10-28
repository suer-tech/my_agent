"""Isolated native-provider vs CLI comparison. No tool execution or live writes."""
import json, os, sys, subprocess, tempfile, time, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from iva_integration import gateway
from local_finance.codex_cli import Codex

REPORT=ROOT/'data/provider-comparison.json'
APP=ROOT/'data/iva-pilot/app'
BASE=("Ты Ива, личная помощница Ильшата. Общайся по-русски, тепло, просто и кратко, "
      "без канцелярита. Сегодня 20 октября 2026 года, часовой пояс Asia/Yekaterinburg. "
      "Ясное поручение владельца достаточно для записи: не требуй подтверждений или кодов. "
      "Не утверждай, что сохранила данные, до успешного результата инструмента. "
      "В этой изолированной проверке все данные вымышленные.")
TOOLS=[
 {'type':'function','function':{'name':'finance_agent','description':'Финансовый помощник: учет доходов и расходов, расчеты и планы.',
 'parameters':{'type':'object','properties':{'task':{'type':'string'}},'required':['task'],'additionalProperties':False}}},
 {'type':'function','function':{'name':'personal_calendar','description':'Календарь и напоминания. Добавление через action=add; title, date (YYYY-MM-DD), time (HH:MM), reminder_minutes.',
 'parameters':{'type':'object','properties':{'action':{'type':'string'},'title':{'type':'string'},'date':{'type':'string'},'time':{'type':'string'},'reminder_minutes':{'type':'integer'}},'required':['action'],'additionalProperties':False}}}
]
CASES=[
 ('conversation','Сегодня совсем вымотался, работа, долги, домашние дела. Не хочу сейчас длинных планов — просто помоги немного собраться.'),
 ('expense','Запиши: сегодня потратил 858 рублей на продукты.'),
 ('reminder','Напомни мне завтра в 15:00 позвонить заказчику.')
]
report={'model':'gpt-6-luna','effort':'low','scope':'Provider comparison; synthetic context and non-executable tools. No Telegram or real finance/calendar writes. Not full Eve integration.', 'cases':[]}
def persist(): REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

with tempfile.TemporaryDirectory(prefix='iva-provider-') as temp:
    tmp=Path(temp);os.chmod(tmp,0o700)
    native_script=APP/'diagnostic-provider-comparison.mjs'
    shutil.copyfile(ROOT/'deploy/compare_iva_provider.mjs',native_script)
    try:
        for name,question in CASES:
            messages=[{'role':'system','content':BASE},{'role':'user','content':question}]
            start=time.monotonic()
            text,calls,usage,execution=gateway.model_answer({'messages':messages,'tools':TOOLS})
            cli={'text':text,'calls':[{'name':c['function']['name'],'args':json.loads(c['function']['arguments'])} for c in calls],
                 'seconds':round(time.monotonic()-start,2)}
            mock=({'ok':True,'saved':True,'amount_rub':'858.00','category':'продукты','date':'2026-10-20'}
                  if name=='expense' else {'ok':True,'saved':True,'title':'Позвонить заказчику',
                    'next_occurrence_at':'2026-10-21T15:00:00+05:00',
                    'next_notification_at':'2026-10-21T15:00:00+05:00','scheduler':{'dispatcher_alive':True}})
            if calls:
                follow_messages=messages+[{'role':'assistant','content':text or None,'tool_calls':calls}]+[
                    {'role':'tool','tool_call_id':c['id'],'content':json.dumps(mock,ensure_ascii=False)} for c in calls]
                ft,fc,_,_=gateway.model_answer({'messages':follow_messages,'tools':TOOLS})
                cli['after_mock_result']={'text':ft,'calls':fc}
            # CLI has just maintained its own login. Share only its short-lived access
            # token with the isolated native store; never copy/rotate refresh credentials.
            auth=json.loads((Codex().original_home/'auth.json').read_text())['tokens']
            (tmp/'codex-auth.json').write_text(json.dumps({
              'access_token':auth['access_token'],'accountId':auth.get('account_id'),'planType':None
            }))
            os.chmod(tmp/'codex-auth.json',0o600)
            inp=tmp/'input.json';out=tmp/'output.json'
            inp.write_text(json.dumps({'system':BASE,'messages':messages[1:],'tools':TOOLS,'mock_result':mock},ensure_ascii=False))
            native_env=dict(os.environ,MODEL_PROVIDER='codex',CODEX_MODEL='gpt-6-luna',
                THINKING_EFFORT='low',ASSISTANT_DATA_DIR=str(tmp),
                NODE_USE_ENV_PROXY='1',HTTPS_PROXY='http://10.203.77.2:8888',
                HTTP_PROXY='http://10.203.77.2:8888',NO_PROXY='localhost,127.0.0.1')
            run=subprocess.run(['/usr/local/bin/node',str(native_script),str(inp),str(out)],
                cwd=APP,env=native_env,capture_output=True,text=True,timeout=110)
            native=json.loads(out.read_text()) if out.exists() else {'error':'node_start_failed','detail':run.stderr[-1200:]}
            row={'name':name,'request':question,'cli':cli,'native':native}
            report['cases'].append(row);persist()
            print(name,'complete',flush=True)
            if 'error' in native:break
    finally:
        native_script.unlink(missing_ok=True)
persist()
