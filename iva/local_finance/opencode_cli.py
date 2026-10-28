"""Isolated, text-only OpenCode inference. Never executes Iva or CLI tools."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid
import urllib.request
import urllib.parse
import base64

ROOT=Path(__file__).resolve().parents[1]
MODEL='opencode/deepseek-v4.1-flash-free'


class OpenCodeError(RuntimeError):pass


def configuration(root=ROOT):
    path=Path(root)/'data/opencode-primary.json'
    if not path.is_file():return {'enabled':False}
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('model')!=MODEL:raise OpenCodeError('Неверная модель первичной обработки OpenCode')
    if any(not isinstance(data.get(key),str) or not data[key] for key in ('executable','state_root')):raise OpenCodeError('OpenCode не настроен')
    return data


def available(root=ROOT):
    try:
        cfg=configuration(root)
        return bool(cfg.get('enabled') and Path(cfg.get('executable','')).is_file())
    except (ValueError,OSError,OpenCodeError):return False


def cli_config():
    # Keep the official build harness. The non-interactive CLI automatically
    # REJECTS every permission request unless --auto is passed (never used here).
    # An explicit deny removes the native tool descriptors and currently causes
    # a false free-tier 403, including in the official CLI.
    return {'$schema':'https://opencode.ai/config.json','model':MODEL,'small_model':MODEL,
            'enabled_providers':['opencode'],'autoupdate':False,'share':'disabled','snapshot':False,
            'permission':{'*':'ask'},'mcp':{},'plugin':[],
            'provider':{'opencode':{'models':{'deepseek-v4.1-flash-free':{'variants':{'iva-fast':{'reasoningEffort':'none','thinking':{'type':'disabled'}}}}}}},
            'agent':{'build':{'permission':{'*':'ask'},'steps':1}}}


def cancel_session(cfg,title):
    """Cancel only this invocation on the persistent server after CLI timeout."""
    if not cfg.get('server_url'):return
    key=Path(cfg['password_file']).read_text(encoding='utf-8').strip()
    headers={'Authorization':'Basic '+base64.b64encode(('opencode:'+key).encode()).decode()}
    try:
        with urllib.request.urlopen(urllib.request.Request(cfg['server_url']+'/session?'+urllib.parse.urlencode({'directory':str(Path(cfg['state_root'])/'work')}),headers=headers),timeout=2) as response:sessions=json.load(response)
        for session in sessions:
            if session.get('title')==title:
                request=urllib.request.Request(cfg['server_url']+'/session/'+urllib.parse.quote(session['id'],safe='')+'/abort',data=b'',headers=headers,method='POST')
                with urllib.request.urlopen(request,timeout=2) as response:response.read()
    except (OSError,ValueError,KeyError):pass


def read_session_answer(cfg,title):
    """The CLI attach event stream can close before the final text event.

    Read back its completed result by the unique title; never resubmit a prompt.
    """
    key=Path(cfg['password_file']).read_text(encoding='utf-8').strip()
    headers={'Authorization':'Basic '+base64.b64encode(('opencode:'+key).encode()).decode()}
    def get(path):
        with urllib.request.urlopen(urllib.request.Request(cfg['server_url']+path,headers=headers),timeout=3) as response:return json.load(response)
    sessions=get('/session?'+urllib.parse.urlencode({'directory':str(Path(cfg['state_root'])/'work')}))
    matches=[s for s in sessions if s.get('title')==title]
    if len(matches)!=1:raise OpenCodeError('OpenCode не подтвердил текущую сессию')
    messages=get('/session/'+urllib.parse.quote(matches[0]['id'],safe='')+'/message')
    answers=[m for m in messages if m.get('info',{}).get('role')=='assistant']
    if not answers:raise OpenCodeError('OpenCode не завершил текущий ответ')
    for message in answers:
        if any(p.get('type')=='tool' for p in message.get('parts',[])):raise OpenCodeError('Нативные инструменты OpenCode запрещены')
    answer=answers[-1];info=answer['info']
    if info.get('modelID')!='deepseek-v4.1-flash-free' or info.get('providerID')!='opencode':raise OpenCodeError('OpenCode использовал другую модель')
    if info.get('error') or not info.get('time',{}).get('completed') or info.get('finish')!='stop':raise OpenCodeError('OpenCode не завершил текущий ответ')
    text=''.join(p['text'] for p in answer.get('parts',[]) if p.get('type')=='text' and isinstance(p.get('text'),str))
    return text,{'actual_model':info['providerID']+'/'+info['modelID'],'tokens':info.get('tokens',{}),'cost':info.get('cost')}


class OpenCode:
    def __init__(self,root=ROOT):self.root=Path(root);self.last_execution={}

    def decide(self,prompt):
        cfg=configuration(self.root)
        if not available(self.root):raise OpenCodeError('OpenCode не настроен')
        if cfg.get('server_url'):
            if not re.fullmatch(r'http://127\.0\.0\.1:\d{2,5}',cfg['server_url']):raise OpenCodeError('OpenCode server must be loopback')
            from .opencode_service import run
            try:value,self.last_execution=run(cfg,prompt,'Iva primary '+uuid.uuid4().hex)
            except OpenCodeError as error:
                self.last_execution={'model':MODEL,'failure':getattr(error,'diagnostics',{'stage':'unknown'})}
                raise
            return value
        runtime=Path(cfg['state_root']);work=runtime/'work'
        work.mkdir(parents=True,exist_ok=True,mode=0o700)
        for name in ('home','config','data','cache'):(runtime/name).mkdir(exist_ok=True,mode=0o700)
        child={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','LANG')}
        child.update(HOME=str(runtime/'home'),XDG_CONFIG_HOME=str(runtime/'config'),
                     XDG_DATA_HOME=str(runtime/'data'),XDG_CACHE_HOME=str(runtime/'cache'),
                     OPENCODE_CONFIG_CONTENT=json.dumps(cli_config()),OPENCODE_DISABLE_AUTOUPDATE='true',
                     OPENCODE_DISABLE_PROJECT_CONFIG='true')
        title='Iva primary '+uuid.uuid4().hex
        command=[cfg['executable'],'run','--model',MODEL,'--agent','build','--variant','iva-fast','--format','json','--title',title]
        if cfg.get('server_url'):
            if not re.fullmatch(r'http://127\.0\.0\.1:\d{2,5}',cfg['server_url']):raise OpenCodeError('OpenCode server must be loopback')
            child['OPENCODE_SERVER_PASSWORD']=Path(cfg['password_file']).read_text(encoding='utf-8').strip()
            command+=['--attach',cfg['server_url']]
        started=time.monotonic()
        process=subprocess.Popen(command,cwd=work,env=child,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,start_new_session=os.name!='nt',
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:out,err=process.communicate(prompt.encode('utf-8'),timeout=min(max(float(cfg.get('timeout_seconds',12)),2),20))
        except subprocess.TimeoutExpired:
            if os.name=='nt':process.kill()
            else:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            process.communicate();self.last_execution={'model':MODEL,'elapsed_ms':round((time.monotonic()-started)*1000),'timeout':True}
            cancel_session(cfg,title)
            raise OpenCodeError('OpenCode превысил время первичной обработки') from None
        self.last_execution={'model':MODEL,'elapsed_ms':round((time.monotonic()-started)*1000),'prompt_chars':len(prompt)}
        if process.returncode:
            cancel_session(cfg,title);raise OpenCodeError('OpenCode недоступен или отклонил запрос')
        text=[]
        for line in out.decode('utf-8',errors='replace').splitlines():
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')=='error':
                cancel_session(cfg,title);raise OpenCodeError('OpenCode вернул ошибку модели')
            part=event.get('part') or {}
            if event.get('type') in ('tool_use','tool-call') or part.get('type')=='tool':
                cancel_session(cfg,title);raise OpenCodeError('Нативные инструменты OpenCode запрещены')
            if event.get('type')=='text' and isinstance(part.get('text'),str):text.append(part['text'])
        if cfg.get('server_url'):
            try:body,usage=read_session_answer(cfg,title)
            except (OpenCodeError,OSError,ValueError,KeyError):
                cancel_session(cfg,title);raise OpenCodeError('OpenCode не подтвердил завершённый ответ') from None
            text=[body];self.last_execution.update(usage)
        if not text:raise OpenCodeError('OpenCode не вернул ответа')
        from iva_integration.routing_validation import strict_object
        try:return strict_object(''.join(text).strip())
        except ValueError:raise OpenCodeError('OpenCode вернул некорректное решение') from None
