import json
import contextlib
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib


ACTIONS = ['none','intake','batch','bank_import','bank_bind','bank_amend','card_save','card_bind','card_scenario','debt_save','debt_status','debt_bind','debt_term_info','utility_save','utility_quote','utility_status','utility_bind','snapshot','balances','forecast','calendar','loan_scenario','account','stage','schedule','configure','loan','void','cancel_schedule']
ACTIONS += ['family_opening','income_save','income_group_save','income_receive','family_expense_save','family_expense_status','family_forecast','family_scenarios','family_allocation','debt_inventory']
ACTIONS += ['family_expense_policy']
ACTIONS.append('income_history')
ACTIONS.append('family_debt_save')
ACTIONS += ['document_context','conversation_context']
SCHEMA = {'type':'object','properties':{'reply':{'type':'string'},'action':{'type':'string','enum':ACTIONS},'payload_json':{'type':'string'}},'required':['reply','action','payload_json'],'additionalProperties':False}


class CliError(RuntimeError): pass


class StructuredOutputError(CliError):
    """Malformed model output; unlike auth/timeout failures, safe to regenerate."""
    pass


def sync_refreshed_login(original,temporary,baseline):
    """Keep an OAuth refresh, but never overwrite a login changed by another process."""
    if not temporary.is_file() or not original.is_file(): return
    updated = temporary.read_bytes()
    if hashlib.sha256(updated).digest()==baseline: return
    if hashlib.sha256(original.read_bytes()).digest()!=baseline: return
    try:
        json.loads(updated)  # Refuse a truncated auth file after an interrupted CLI.
        with tempfile.NamedTemporaryFile(dir=original.parent,prefix='finance-auth-',delete=False) as target:
            path = Path(target.name)
            target.write(updated)
        os.chmod(path,0o600)
        os.replace(path,original)
    except (OSError,ValueError):
        if 'path' in locals(): path.unlink(missing_ok=True)
        raise CliError('Не удалось сохранить обновление CLI-login; выполните codex login') from None


def executable():
    override = os.environ.get('FINANCE_CODEX_EXE')
    if override:
        p = Path(override)
        if not p.is_file() or p.suffix.lower() not in ('.exe',''):
            raise CliError('FINANCE_CODEX_EXE должен указывать на исполняемый файл CLI')
        return str(p)
    found = shutil.which('codex.exe')
    if found: return found
    # npm's Windows .cmd/.ps1 wrapper needs a shell; run the signed-up native binary instead.
    root = Path(os.environ.get('APPDATA',''))/'npm/node_modules/@openai/codex'
    matches = list(root.glob('node_modules/**/codex.exe'))
    if matches: return str(matches[0])
    found = shutil.which('codex')
    if found and Path(found).suffix.lower() not in ('.cmd','.ps1','.bat'): return found
    raise CliError('Codex CLI не найден; установите его или задайте FINANCE_CODEX_EXE')


class Codex:
    def __init__(self, timeout=150, role=None):
        self.timeout = timeout
        self.role=role
        self.original_home = Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
        config = self.original_home/'config.toml'
        settings = tomllib.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
        self.model = os.environ.get('FINANCE_CODEX_MODEL',settings.get('model',''))
        self.service_tier=os.environ.get('IVA_SERVICE_TIER')
        self.reasoning_effort=self.context_window=self.auto_compact_token_limit=self.iva_context_window=None
        self._load_runtime_settings()

    def _load_runtime_settings(self):
        runtime_path=Path(__file__).resolve().parents[1]/'data/iva-model.json'
        if not runtime_path.exists():return
        runtime=json.loads(runtime_path.read_text(encoding='utf-8'))
        self.model=runtime['model'];self.reasoning_effort=runtime['reasoning_effort']
        self.context_window=runtime['cli_context_window']
        self.auto_compact_token_limit=runtime['cli_auto_compact_token_limit']
        self.iva_context_window=runtime['iva_context_window']
        self.service_tier=runtime.get('service_tier',self.service_tier)
        if self.role:
            profiles=json.loads((runtime_path.parent/'iva-agents.json').read_text(encoding='utf-8'))
            profile=profiles[self.role]
            self.model=profile['model'];self.reasoning_effort=profile['reasoning_effort']
            self.service_tier=profile.get('service_tier',self.service_tier)
        if self.service_tier not in (None,'fast'):raise CliError('Некорректный режим скорости Ива')
        if self.reasoning_effort is not None and self.reasoning_effort not in ('none','low','medium','high','xhigh','max'):raise CliError('Некорректный уровень рассуждения Ива')
        for value in (self.context_window,self.auto_compact_token_limit,self.iva_context_window):
            if value is not None and (type(value)!=int or value<=0):raise CliError('Некорректное окно контекста Ива')

    def command(self, work, schema, output,images=()):
        # Also updates a long-lived Telegram document extractor without restarting
        # the poller. The adapter never falls back to a heavier model on failure.
        self._load_runtime_settings()
        args = [executable(),'exec','--skip-git-repo-check','--sandbox','read-only','--ephemeral','--json',
                '-C',str(work),'--output-schema',str(schema),'--output-last-message',str(output),
                '-c','approval_policy="never"','-c','web_search="disabled"',
                '-c','features.shell_tool=false','-c','features.apply_patch_freeform=false',
                '-c','features.apps=false','-c','features.plugins=false','-c','features.multi_agent=false',
                '-c','features.js_repl=false','-c','features.image_generation=false','-c','features.memories=false']
        if self.model: args += ['--model',self.model]
        if self.service_tier=='fast':args+=['-c','service_tier="fast"','-c','features.fast_mode=true']
        if self.reasoning_effort is not None:args+=['-c','model_reasoning_effort='+json.dumps(self.reasoning_effort)]
        if self.context_window is not None:args+=['-c','model_context_window='+str(self.context_window)]
        if self.auto_compact_token_limit is not None:args+=['-c','model_auto_compact_token_limit='+str(self.auto_compact_token_limit)]
        for image in images:
            path=Path(image).resolve()
            if not path.is_file() or path.suffix.lower() not in ('.png','.jpg','.jpeg'):raise CliError('Неподдерживаемое изображение для CLI')
            args+=['--image',str(path)]
        return args+['-']

    def structured(self,prompt,schema,images=()):return self.plan(prompt,output_schema=schema,images=images)

    def plan(self, prompt,output_schema=None,images=()):
        self.last_usage = None
        auth = self.original_home/'auth.json'
        if not auth.is_file():
            raise CliError('Нет файлового CLI-login. Выполните codex login; keyring-only login пока не поддержан адаптером')
        # Private, short-lived CODEX_HOME: reuse login but never inherit user MCP,
        # skills, hooks, apps, provider keys or incompatible user config.
        from .shared_auth import login_lock
        with login_lock(self.original_home), tempfile.TemporaryDirectory(prefix='finance-cli-',dir=self.original_home) as tmp, contextlib.ExitStack() as finalize:
            root = Path(tmp)
            home,work = root/'home',root/'work'
            home.mkdir(mode=0o700); work.mkdir(mode=0o700)
            shutil.copyfile(auth,home/'auth.json')
            os.chmod(home/'auth.json',0o600)
            baseline = hashlib.sha256((home/'auth.json').read_bytes()).digest()
            finalize.callback(sync_refreshed_login,auth,home/'auth.json',baseline)
            schema,output = root/'schema.json',root/'answer.json'
            schema.write_text(json.dumps(output_schema or SCHEMA),encoding='utf-8')
            env = {k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('TOKEN','API_KEY','SECRET','PASSWORD'))}
            env['CODEX_HOME'] = str(home)
            env.pop('CODEX_THREAD_ID',None)
            env.pop('CODEX_INTERNAL_ORIGINATOR_OVERRIDE',None)
            flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            command=self.command(work,schema,output,images)
            self.last_execution={'model':self.model,'reasoning_effort':self.reasoning_effort,'service_tier':self.service_tier,'cli_context_window':self.context_window,'cli_auto_compact_token_limit':self.auto_compact_token_limit,'images':len(images),'explicit_command_model':command[command.index('--model')+1] if '--model' in command else None,'explicit_reasoning_override':('model_reasoning_effort='+json.dumps(self.reasoning_effort)) in command if self.reasoning_effort is not None else False,'explicit_fast_mode':'service_tier="fast"' in command and 'features.fast_mode=true' in command}
            process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,creationflags=flags)
            try:
                stdout,stderr = process.communicate(prompt.encode('utf-8'),timeout=self.timeout)
            except subprocess.TimeoutExpired:
                if os.name=='nt': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=flags)
                else: process.kill()
                process.communicate()
                raise CliError('Codex CLI превысил таймаут; финансовые изменения не выполнены') from None
            # Never pass raw stderr/JSON event stream to chat or log: may contain paths or credentials.
            if process.returncode or not output.is_file():
                lower = (stderr+stdout).lower()
                if b'401' in lower or b'login' in lower or b'unauthorized' in lower:
                    raise CliError('CLI-login истёк или недоступен. Выполните codex login')
                if b'usage limit' in lower or b'quota' in lower or b'rate limit' in lower:
                    raise CliError('Достигнут лимит Codex; повторите позже')
                raise CliError(f'Codex CLI завершился с ошибкой ({process.returncode}); записи не изменены')
            for line in stdout.decode('utf-8',errors='replace').splitlines():
                try: event = json.loads(line)
                except ValueError: continue
                if event.get('type')=='turn.completed' and isinstance(event.get('usage'),dict):
                    self.last_usage = event['usage']
                item = event.get('item',{})
                if item.get('type') in ('command_execution','mcp_tool_call','file_change','web_search'):
                    raise CliError('CLI попытался использовать запрещённый инструмент; ответ отклонён')
            try:
                value = json.loads(output.read_text(encoding='utf-8'))
                if output_schema is not None:
                    if not isinstance(value,dict):raise ValueError()
                    return value
                if set(value)!=set(SCHEMA['required']) or value['action'] not in ACTIONS or not isinstance(value['reply'],str): raise ValueError()
                payload = json.loads(value['payload_json'])
                if not isinstance(payload,dict): raise ValueError()
                return value['reply'],value['action'],payload
            except (ValueError,TypeError,KeyError):
                raise StructuredOutputError('CLI вернул некорректный структурированный ответ; записи не изменены') from None
