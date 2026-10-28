"""Official `opencode serve` client; every native tool request is rejected."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import time
import urllib.request
import urllib.parse
import urllib.error


@contextmanager
def invocation_lock(cfg):
    """Keep watchdog restarts outside the entire inference and abort boundary."""
    if os.name!='posix':
        yield
        return
    import fcntl
    from .opencode_cli import OpenCodeError
    stream=None;acquired=False
    try:
        try:
            # Installed privately beside the existing server password. Absence
            # is a setup failure, never permission to run without coordination.
            descriptor=os.open(Path(cfg['password_file']).parent/'inference.lock',
                               os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0))
            stream=os.fdopen(descriptor,'rb')
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise OSError('Invocation lock must be a regular file')
            fcntl.flock(stream.fileno(),fcntl.LOCK_SH|fcntl.LOCK_NB)
            acquired=True
        except OSError as error:
            failure=OpenCodeError('OpenCode временно недоступен для запроса')
            failure.diagnostics={'stage':'invocation_lock','category':'maintenance_busy' if isinstance(error,BlockingIOError) else 'lock_unavailable',
                                 'kind':type(error).__name__,'cleanup_attempted':False,'cleanup_confirmed':False}
            raise failure from None
        yield
    finally:
        if stream is not None:
            if acquired:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            stream.close()


def _provider_ready(value):
    """The public capability response is not proof of successful inference."""
    if not isinstance(value,dict) or not isinstance(value.get('all'),list) or not isinstance(value.get('connected'),list):
        return False
    if 'opencode' not in value['connected']:
        return False
    return any(isinstance(provider,dict) and provider.get('id')=='opencode'
               and isinstance(provider.get('models'),dict) and 'deepseek-v4.1-flash-free' in provider['models']
               for provider in value['all'])


def _provider_error_metadata(error):
    """Never retain provider messages, response bodies, headers or arbitrary names."""
    error=error if isinstance(error,dict) else {}
    names={'APIError','ProviderAuthError','UnknownError','MessageAbortedError','ContextOverflowError','StructuredOutputError'}
    name=error.get('name')
    result={'provider_error_name':name if isinstance(name,str) and name in names else 'OtherProviderError'}
    data=error.get('data') if isinstance(error.get('data'),dict) else {}
    status=data.get('statusCode')
    if type(status) is int and 100<=status<=599:result['provider_http_status']=status
    if type(data.get('isRetryable')) is bool:result['provider_retryable']=data['isRetryable']
    return result


def run(cfg,prompt,title):
    with invocation_lock(cfg):
        return _run_locked(cfg,prompt,title)


def _run_locked(cfg,prompt,title):
    from .opencode_cli import OpenCodeError,MODEL
    password=Path(cfg['password_file']).read_text(encoding='utf-8').strip()
    headers={'Authorization':'Basic '+base64.b64encode(('opencode:'+password).encode()).decode(),'Content-Type':'application/json'}
    started=time.monotonic();deadline=started+min(max(float(cfg.get('timeout_seconds',12)),2),20)
    query=urllib.parse.urlencode({'directory':str(Path(cfg['state_root'])/'work')})
    stage='provider_preflight';details={};failure=None
    def request(path,data=None,method=None,cleanup=False,limit=8):
        # Session initialization/prompt acceptance may exceed three seconds on
        # this small VPS. Keep the overall deadline, rather than failing early.
        remaining=2 if cleanup else min(limit,deadline-time.monotonic())
        if remaining<=0:raise OpenCodeError('OpenCode превысил время первичной обработки')
        body=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
        req=urllib.request.Request(cfg['server_url']+path+'?'+query,data=body,headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=remaining) as response:
            content=response.read();return json.loads(content) if content else None
    session_id=None;done=False
    try:
        # A healthy HTTP process can have a failed provider scope. Detect that
        # before creating a session that would otherwise wait until deadline.
        details['category']='provider_unavailable'
        if not _provider_ready(request('/provider',limit=3)):
            raise OpenCodeError('OpenCode не подтвердил доступность выбранной модели')
        details.pop('category',None)
        stage='create_session'
        session=request('/session',{'title':title,'permission':[{'permission':'*','pattern':'*','action':'ask'},
            {'permission':'question','pattern':'*','action':'deny'},
            {'permission':'plan_enter','pattern':'*','action':'deny'},
            {'permission':'plan_exit','pattern':'*','action':'deny'}]})
        session_id=session['id'];path='/session/'+urllib.parse.quote(session_id,safe='')
        stage='submit_prompt'
        try:
            request(path+'/prompt_async',{'agent':'build','model':{'providerID':'opencode','modelID':'deepseek-v4.1-flash-free'},
                'variant':'iva-fast','parts':[{'type':'text','text':prompt}]})
        except (TimeoutError,urllib.error.URLError) as error:
            # Acceptance may already have happened. Read this same session;
            # never submit the prompt again after an uncertain HTTP response.
            if isinstance(error,urllib.error.HTTPError):raise
        while time.monotonic()<deadline:
            # Match the official non-interactive CLI's auto-reject behavior.
            stage='read_permissions';native_requests=request('/permission')
            for permission in native_requests:
                if permission.get('sessionID')==session_id:
                    request('/permission/'+urllib.parse.quote(permission['id'],safe='')+'/reply',{'reply':'reject'})
                    raise OpenCodeError('Нативные инструменты OpenCode запрещены')
            stage='read_messages';messages=request(path+'/message')
            answers=[m for m in messages if m.get('info',{}).get('role')=='assistant']
            for message in answers:
                if any(p.get('type')=='tool' for p in message.get('parts',[])):raise OpenCodeError('Нативные инструменты OpenCode запрещены')
            if answers:
                answer=answers[-1];info=answer['info']
                if info.get('error'):
                    stage='provider_error';details.update(category='provider_rejected',**_provider_error_metadata(info['error']))
                    raise OpenCodeError('OpenCode отклонил запрос')
                if info.get('time',{}).get('completed'):
                    if info.get('modelID')!='deepseek-v4.1-flash-free' or info.get('providerID')!='opencode' or info.get('finish')!='stop':raise OpenCodeError('OpenCode не подтвердил нужную модель и завершение ответа')
                    text=''.join(p['text'] for p in answer.get('parts',[]) if p.get('type')=='text' and isinstance(p.get('text'),str))
                    from iva_integration.routing_validation import strict_object
                    value=strict_object(text.strip());done=True
                    return value,{'model':MODEL,'actual_model':MODEL,'transport':'opencode-cli-serve','elapsed_ms':round((time.monotonic()-started)*1000),
                                  'prompt_chars':len(prompt),'tokens':info.get('tokens',{}),'cost':info.get('cost')}
            time.sleep(.15)
        stage='deadline';raise OpenCodeError('OpenCode превысил время первичной обработки')
    except (OSError,ValueError,KeyError,TypeError,OpenCodeError) as error:
        failure=OpenCodeError('OpenCode недоступен или не подтвердил ответ')
        failure.diagnostics={'stage':stage,'kind':type(error).__name__,'elapsed_ms':round((time.monotonic()-started)*1000),**details}
        if isinstance(error,urllib.error.HTTPError):
            failure.diagnostics['http_status']=error.code
            error.close()
        raise failure from None
    finally:
        attempted=False;confirmed=False
        if session_id and not done:
            attempted=True
            try:confirmed=request('/session/'+urllib.parse.quote(session_id,safe='')+'/abort',{},cleanup=True) is True
            except (OSError,ValueError,OpenCodeError):pass
        if failure is not None:
            failure.diagnostics.update(cleanup_attempted=attempted,cleanup_confirmed=confirmed)
