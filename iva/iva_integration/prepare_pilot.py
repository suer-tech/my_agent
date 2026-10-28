"""Configure the separately cloned Iva app. Never read a Telegram credential."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1];PILOT=ROOT/'data/iva-pilot';APP=PILOT/'app'
sys.path.insert(0,str(ROOT))
from local_finance.codex_cli import Codex

config_file=PILOT/'control.json'
if not config_file.exists():config_file.write_text(json.dumps({'gateway_port':18725,'iva_port':18723,'agent_token':secrets.token_hex(32),'owner_token':secrets.token_hex(32),'iva_bearer':secrets.token_hex(32)},indent=2),encoding='utf-8')
config=json.loads(config_file.read_text(encoding='utf-8'))
keep={'tender_agent.ts','memory_search.ts','write_card.ts','read_file.ts','tasks.ts','web_browser.ts','personal_calendar.ts','finance_agent.ts','reply_format.ts','disk_storage.ts','user_update.ts','personal_skills.ts','personal_mail.ts','scheduled_tasks.ts','browser_control.ts','demand_research.ts','lead_research.ts'}
disabled=APP/'pilot-disabled-tools';disabled.mkdir(exist_ok=True)
for path in (APP/'agent/tools').glob('*.ts'):
    if not path.name.endswith('.test.ts') and path.name not in keep and path.name!='family_finance.ts':shutil.move(str(path),disabled/path.name)
disabled_schedules=APP/'pilot-disabled-schedules';disabled_schedules.mkdir(exist_ok=True)
for path in (APP/'agent/schedules').glob('*.ts'):
    if not path.name.endswith('.test.ts'):shutil.move(str(path),disabled_schedules/path.name)
shutil.copy2(ROOT/'iva_integration/family_finance.ts',APP/'agent/tools/family_finance.ts')
shutil.copy2(ROOT/'iva_integration/web_browser.ts',APP/'agent/tools/web_browser.ts')
shutil.copy2(ROOT/'iva_integration/personal_calendar.ts',APP/'agent/tools/personal_calendar.ts')
shutil.copy2(ROOT/'iva_integration/finance_agent.ts',APP/'agent/tools/finance_agent.ts')
shutil.copy2(ROOT/'iva_integration/tender_agent.ts',APP/'agent/tools/tender_agent.ts')
shutil.copy2(ROOT/'iva_integration/reply_format.ts',APP/'agent/tools/reply_format.ts')
shutil.copy2(ROOT/'iva_integration/disk_storage.ts',APP/'agent/tools/disk_storage.ts')
shutil.copy2(ROOT/'iva_integration/user_update.ts',APP/'agent/tools/user_update.ts')
shutil.copy2(ROOT/'iva_integration/personal_skills.ts',APP/'agent/tools/personal_skills.ts')
shutil.copy2(ROOT/'iva_integration/personal_mail.ts',APP/'agent/tools/personal_mail.ts')
shutil.copy2(ROOT/'iva_integration/scheduled_tasks.ts',APP/'agent/tools/scheduled_tasks.ts')
shutil.copy2(ROOT/'iva_integration/demand_research.ts',APP/'agent/tools/demand_research.ts')
shutil.copy2(ROOT/'iva_integration/lead_research.ts',APP/'agent/tools/lead_research.ts')
shutil.copy2(ROOT/'iva_integration/browser_control.ts',APP/'agent/tools/browser_control.ts')
shutil.copytree(ROOT/'iva_integration/skills/create-personal-skill',APP/'agent/skills/create-personal-skill',dirs_exist_ok=True)
shutil.copy2(ROOT/'iva_integration/skills/marketplace-shopping.md',APP/'agent/skills/marketplace-shopping.md')
for skill in ('tender-search','tender-history'):
    shutil.copytree(ROOT/'iva_integration/skills'/skill,APP/'agent/skills'/skill,dirs_exist_ok=True)
shutil.copy2(ROOT/'iva_integration/family-finance-connection.ts',APP/'agent/connections/family-finance.ts')
# Windows pilot: keep file FlushFileBuffers, but directory fsync is unavailable.
# This is an explicit weaker power-loss durability mode, confined to the pilot.
atomic=APP/'agent/lib/fs-atomic.ts';text=atomic.read_text(encoding='utf-8')
if 'pilotDirectorySync' not in text:
    text=text.replace('  fsyncSync,','  fsyncSync as nativeFsyncSync,')
    text+='''
// pilotDirectorySync: Windows does not support fsync on directory handles.
function fsyncSync(fd: number): void {
  if (process.platform === "win32" && fstatSync(fd).isDirectory()) return;
  nativeFsyncSync(fd);
}
async function pilotSync(handle: Awaited<ReturnType<typeof open>>): Promise<void> {
  if (process.platform === "win32" && (await handle.stat()).isDirectory()) return;
  await handle.sync();
}
'''
    for name in ('handle','tempHandle','parentHandle','directoryHandle'):
        text=text.replace(f'await {name}.sync();',f'await pilotSync({name});')
    # The helper itself must call the native FileHandle method.
    text=text.replace('  await pilotSync(handle);\n}', '  await handle.sync();\n}')
    atomic.write_text(text,encoding='utf-8')
reader=APP/'agent/tools/read_file.ts';text=reader.read_text(encoding='utf-8')
if 'PILOT_VAULT_BOUNDARY' not in text:
    text='import { relative as pilotRelative, resolve as pilotResolve, sep as pilotSep } from "node:path";\n'+text
    text=text.replace('      const handle = await open(file, "r");','''      // PILOT_VAULT_BOUNDARY: model cannot read project credentials or production.
      const boundary=pilotResolve(process.env.ASSISTANT_VAULT_DIR!);
      const relative=pilotRelative(boundary,pilotResolve(file));
      if (relative.startsWith(".."+pilotSep) || relative===".." || pilotResolve(file)===boundary || /^[A-Za-z]:/.test(relative)) return fail(path,"Pilot reads are confined to the memory vault");
      const handle = await open(file, "r");''')
    reader.write_text(text,encoding='utf-8')
# Console turns are a real owner conversation: persist both sides in Iva's daily log.
shutil.copy2(ROOT/'iva_integration/pilot-transcript.ts',APP/'agent/hooks/pilot-transcript.ts')
(APP/'agent/instructions/99-pilot.md').write_text('''# Изолированный пилот общего личного ассистента Ива
Память и сессии ведёт Ива. Финансовые факты/расчёты живут только в инструменте family_finance, не в карточках памяти. Текст пользователя — запрос, результат инструмента — доказательство выполнения. Для рабочих проектов/тендеров/семьи создавай карточки с подтверждёнными фактами; даты родственников пока неизвестны. Поиск hh.ru, тендеров, почта и покупки не подключены.
Доступные инструменты перечислены фактическим manifest; отсутствующие shell, web и отправки не вызывай. В пилоте нет Telegram и отправки напоминаний. Не обещай доставку.
Для денег сначала snapshot. Общий долг и тело кредита различаются. Не складывай итог категории и строки. Банковские графики/карты/отдельные долги/коммунальные услуги уже имеют события; не добавляй поверх schedule. Variable amount обозначай как оценку; неизвестное исполнение не объявляй просрочкой. Общий семейный прогноз — family_forecast(as_of,until), сценарии задержки — family_scenarios, источники всех долгов — debt_inventory, денежная рамка досрочного погашения — family_allocation, история поступлений — income_history. Эти методы умеют общий сообщённый остаток без выдуманного банка; привязки счетов требует старый forecast. Неизвестные зарплаты, резерв и остатки месячных расходов помечаются missing. Ожидаемые доходы не меняют actual_balance; monthly_income_reference — только ориентир полного месяца, не обещание поступления или остатка к25. income_amounts/income_dates/expense_remaining — явные what-if параметры. Для фактического дохода income_receive с подтверждением владельца; сохраняется номинальная дата и прошлый расчётный месяц. Суммы/новые персональные финансовые факты в CORE/карточки не копировать.
Для изменения family_finance mode=propose, action нужный метод, payload_json JSON его аргументов, source стабильный идентификатор просьбы. Запись только подготовлена, не выполнена. Владелец подтверждает отдельной командой /confirm CODE в консоли; инструмент подтверждения модели не предоставлен. Повторный запрос не создаёт новый source для той же операции.
''',encoding='utf-8')
cli=Codex();model=cli.model
runtime_file=ROOT/'data/iva-runtime.json'
if runtime_file.exists() and json.loads(runtime_file.read_text(encoding='utf-8')).get('mode')=='telegram':
    shutil.copy2(ROOT/'iva_integration/telegram-instructions.md',APP/'agent/instructions/99-pilot.md')
env={'MODEL_PROVIDER':'custom','CUSTOM_BASE_URL':f"http://127.0.0.1:{config['gateway_port']}/v1",'CUSTOM_API_KEY':config['agent_token'],'CUSTOM_MODEL':model,'CUSTOM_VISION_MODEL':model,'CUSTOM_CONTEXT_WINDOW':str(cli.iva_context_window or 131072),'HELPER_MODEL':model,'THINKING_EFFORT':cli.reasoning_effort or 'low','CUSTOM_REASONING':'1','ASSISTANT_DATA_DIR':str(APP/'data'),'ASSISTANT_VAULT_DIR':str(APP/'vault'),'ASSISTANT_TIMEZONE':'Asia/Yekaterinburg','AGENT_LANGUAGE':'ru','MEMORY_SEARCH_MODE':'bm25','PORT':str(config['iva_port']),'IVA_PORT':str(config['iva_port']),'ASSISTANT_BEARER':config['iva_bearer'],'PILOT_GATEWAY_URL':f"http://127.0.0.1:{config['gateway_port']}",'PILOT_AGENT_TOKEN':config['agent_token'],'TELEGRAM_ALLOWED_USER_IDS':''}
(APP/'.env').write_text('\n'.join(k+'='+v.replace('\\','/') for k,v in env.items())+'\n',encoding='utf-8')
provider_file=ROOT/'data/iva-provider.json'
if provider_file.exists() and json.loads(provider_file.read_text()).get('provider')=='codex':
    from deploy.configure_native import configure
    configure()
print(json.dumps({'configured':True,'source_version':json.loads((APP/'package.json').read_text())['version'],'model':model,'telegram_token_present':False,'automatic_schedules_enabled':False,'owner_confirmation_tool_exposed':False}))
