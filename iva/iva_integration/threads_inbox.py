"""Read-only inbox previews. Never opens a conversation, sends a DM or uses AI."""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from iva_integration.threads_alerts import Monitor

ROOT=Path('/opt/iva/data/threads-browser')
SCAN_TIMEOUT_SECONDS=110
FOLDERS=(('inbox',''),('requests','/requests'),('hidden','/hidden'))


class InboxReadError(RuntimeError):
    """Only allowlisted metadata is persisted; never include page/exception text."""
    def __init__(self,code,folder,stage,attempt=1,error_type='RuntimeError'):
        self.code=code;self.folder=folder;self.stage=stage
        self.attempt=attempt;self.error_type=error_type
        super().__init__('Inbox '+code)


def diagnose(root,event):
    """Small bounded history keeps the cause after the next successful scan."""
    allowed=('at','state','folder','stage','attempt','code','error_type','elapsed_seconds','visible_conversations')
    value={key:event[key] for key in allowed if key in event}
    path=Path(root)/'inbox-diagnostics.jsonl'
    if path.exists() and path.stat().st_size>128*1024:
        path.replace(path.with_suffix('.jsonl.1'))
    with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(value,ensure_ascii=False)+'\n')
    path.chmod(0o600)


def transient(exc):
    return type(exc).__name__=='TimeoutError' or (type(exc).__name__=='Error' and 'net::' in str(exc))


def activity(root,stage,deadline_at,state='running'):
    value={'campaign':'inbox','stage':stage,'state':state,'updated_at':time.time(),'deadline_at':deadline_at}
    path=Path(root)/'activity.json';temporary=path.with_suffix('.inbox.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8');temporary.chmod(0o600);temporary.replace(path)


def store(snapshot,root=ROOT,now=None):
    now=time.time() if now is None else now;root=Path(root)
    monitor=Monitor(root=root,recover=False)
    try:
        monitor.db.execute('CREATE TABLE IF NOT EXISTS inbox_seen(key TEXT PRIMARY KEY,unread INTEGER,fingerprint TEXT,updated REAL)')
        added=0
        with monitor.db:
            for item in snapshot['items']:
                key=item['url']+'|'+item.get('sender','');fingerprint=item['fingerprint']
                old=monitor.db.execute('SELECT unread,fingerprint FROM inbox_seen WHERE key=?',(key,)).fetchone()
                if item['unread'] and (not old or not old['unread'] or old['fingerprint']!=fingerprint):
                    ident='dm:'+hashlib.sha256((key+fingerprint+str(now)).encode()).hexdigest()
                    monitor.db.execute("INSERT INTO outbox VALUES (?,?,'pending',NULL,?)",(ident,'Threads: новое входящее сообщение'+(' — '+item['sender'] if item.get('sender') else '')+'.\n'+item['url'],now));added+=1
                monitor.db.execute('INSERT OR REPLACE INTO inbox_seen VALUES (?,?,?,?)',(key,int(item['unread']),fingerprint,now))
        return added
    finally:monitor.close()


def scan(page,on_folder=None,on_event=None):
    items=[];finished=[];started=time.monotonic();deadline=started+SCAN_TIMEOUT_SECONDS
    def event(**value):
        if on_event:on_event({'at':time.time(),'elapsed_seconds':round(time.monotonic()-started,3),**value})
    def budget(maximum,folder,stage,attempt):
        remaining=int((deadline-time.monotonic())*1000)
        if remaining<=0:raise InboxReadError('deadline',folder,stage,attempt,'TimeoutError')
        return min(maximum,remaining)
    for folder,suffix in FOLDERS:
      result=None
      for attempt in (1,2):
       stage='navigate'
       try:
        event(state='reading',folder=folder,stage=stage,attempt=attempt)
        page.goto('https://www.threads.com/messages'+suffix,wait_until='domcontentloaded',timeout=budget(25000,folder,stage,attempt))
        stage='hydrate'
        # Wait for hydrated inbox rather than interpreting an empty loading skeleton as success.
        page.wait_for_function("""() => /Keep it real in direct messages|Отправляйте личные сообщения|No message requests|Нет запросов|Messages from people you don.t follow will appear here|Message requests that may be offensive|How message requests work|Continue with Instagram|Log in or sign up|Войти через Instagram|Confirm you.re human|Подтвердите, что вы человек/i.test(document.body.innerText) || [...document.querySelectorAll('a[href]')].some(a=>/^\\/messages\\/(?!new\\/?$|requests\\/?$|hidden\\/?$|settings\\/?$)(?:t\\/)?[\\w-]+\\/?$/.test(new URL(a.href).pathname)) || [...document.querySelectorAll('button,[role=button]')].some(b=>b.getClientRects().length && !b.closest('nav,[role=navigation],header') && b.querySelector('img[alt]') && b.innerText.trim() && !/loading|загрузка/i.test(b.innerText))""",timeout=budget(20000,folder,stage,attempt))
        if page.get_by_text('How message requests work',exact=True).is_visible():
            # Informational first-use sheet only, not a request acceptance or a settings change.
            page.get_by_role('button',name='OK',exact=True).click(timeout=budget(5000,folder,stage,attempt))
            page.wait_for_timeout(min(1200,budget(1200,folder,stage,attempt)))
        stage='extract'
        result=page.evaluate('''() => {
          const visible=e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
          const text=document.body.innerText;
          const blueDot=a=>[...a.querySelectorAll('*')].some(e=>{const b=e.getBoundingClientRect(),s=getComputedStyle(e),c=s.backgroundColor.match(/\\d+/g);return visible(e)&&b.width>=4&&b.width<=12&&b.height>=4&&b.height<=12&&parseFloat(s.borderRadius)>=4&&c&&+c[2]>+c[0]+30&&+c[2]>100;});
          const rows=[...document.querySelectorAll('a[href]')].filter(visible).filter(a=>/^\\/messages\\/(?!new\\/?$|requests\\/?$|hidden\\/?$|settings\\/?$)(?:t\\/)?[\\w-]+\\/?$/.test(new URL(a.href).pathname));
          for(const b of document.querySelectorAll('button,[role=button]')){
            const box=b.getBoundingClientRect();
            if(visible(b) && !b.closest('nav,[role=navigation],header') && box.height>=40 && box.height<180 && b.querySelector('a[href^="/@"],img[alt]') && b.innerText.trim() && !/loading|загрузка/i.test(b.innerText) && !rows.some(a=>a.contains(b)||b.contains(a)))rows.push(b);
          }
          return {empty:/Keep it real in direct messages|Отправляйте личные сообщения|No message requests|Нет запросов|Messages from people you don.t follow will appear here|Message requests that may be offensive/i.test(text),
            gate:/Continue with Instagram|Log in or sign up|Войти через Instagram/.test(text),
            challenge:/Confirm you.re human|Подтвердите, что вы человек/i.test(text),
            rows:rows.slice(0,100).map(a=>({url:a.href ? a.href.split('?')[0] : location.origin+location.pathname,sender:(a.innerText.split('\\n')[0]||'').slice(0,80),
              unread:!!a.querySelector('[aria-label*="Unread"],[aria-label*="unread"],[aria-label*="Непрочитан"],[data-visualcompletion="ignore-dynamic"] [aria-label*="unread"]') || /unread|непрочитан/i.test(a.getAttribute('aria-label')||'') || blueDot(a),
              // Hash preview locally; private message bodies never enter notifications or model prompts.
              preview:(a.querySelector('time[datetime]')?.getAttribute('datetime')||'')+'\\n'+a.innerText.split('\\n').filter(t=>!/^\\d+\\s*(s|m|h|d|w|sec|min|hr|сек|мин|ч|дн)(\\.| ago)?$/i.test(t.trim())).join('\\n')}))};
        }''')
        if result['gate']:raise InboxReadError('login_required',folder,stage,attempt)
        if result.get('challenge'):raise InboxReadError('challenge_required',folder,stage,attempt)
        location=urlsplit(page.url)
        if location.hostname not in ('www.threads.com','threads.com') or location.path.rstrip('/')!='/messages'+suffix:
            raise InboxReadError('unexpected_page',folder,stage,attempt)
        if not result['empty'] and not result['rows']:raise InboxReadError('layout_unrecognized',folder,stage,attempt)
        break
       except Exception as exc:
        failure=exc if isinstance(exc,InboxReadError) else InboxReadError('timeout' if type(exc).__name__=='TimeoutError' else 'read_failed',folder,stage,attempt,type(exc).__name__)
        retry=attempt==1 and transient(exc) and deadline-time.monotonic()>5
        event(state='retry' if retry else 'error',folder=folder,stage=failure.stage,attempt=attempt,code=failure.code,error_type=failure.error_type)
        if retry:
            page.wait_for_timeout(500)
            continue
        raise failure from None
      if result is not None:
        folder_items=[]
        for row in result['rows']:
            preview=row.pop('preview')
            if any(line.strip().lower().startswith(('you:','вы:','вы отправили')) for line in preview.splitlines()):row['unread']=False
            row['fingerprint']=hashlib.sha256(preview.encode()).hexdigest();items.append(row);folder_items.append(row)
        if on_folder:on_folder(folder_items)
        finished.append(folder)
        event(state='ok',folder=folder,stage='complete',attempt=attempt,visible_conversations=len(folder_items))
    return {'items':items,'folders':finished}


def check(context,root=ROOT,force=False):
    root=Path(root);path=root/'inbox-status.json';now=time.time()
    try:previous=json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):previous={}
    if not force and now-previous.get('checked_at',0)<60:return previous
    value={'checked_at':now,'read_only':True,'conversation_bodies_saved':False,'new_notifications':0,'checked_folders':[]}
    def record(event):
        diagnose(root,event)
        stage='inbox_'+event.get('stage','check')
        if event.get('folder'):stage+='_'+event['folder']
        activity(root,stage,now+SCAN_TIMEOUT_SECONDS)
        if event.get('state')=='ok' and event.get('folder') not in value['checked_folders']:
            value['checked_folders'].append(event['folder'])
    def folder_ready(items):
        added=store({'items':items},root)
        value['new_notifications']+=added
        if added:wake()
    try:
        activity(root,'inbox_setup',now+SCAN_TIMEOUT_SECONDS)
        from iva_integration.threads_page import working_page
        page=working_page(context);original=page.url
        try:
            snapshot=scan(page,on_folder=folder_ready,on_event=record)
            value.update(state='ok',visible_conversations=len(snapshot['items']),folders=snapshot['folders'],consecutive_failures=0,last_success_at=time.time())
        finally:
            activity(root,'inbox_restore',time.time()+15)
            try:page.goto(original,wait_until='domcontentloaded',timeout=15000)
            except Exception as exc:
                # Inbox completion and restoration are separate facts; do not erase a
                # successful read or mask its original failure with a later timeout.
                value['restore_error']=type(exc).__name__
                record({'at':time.time(),'state':'error','stage':'restore','code':'restore_failed','error_type':type(exc).__name__})
    except Exception as exc:
        # Do not persist page text, previews or arbitrary exception URLs.
        value.update(state='error',error=type(exc).__name__,consecutive_failures=previous.get('consecutive_failures',0)+1)
        if previous.get('last_success_at'):value['last_success_at']=previous['last_success_at']
        if isinstance(exc,InboxReadError):
            value.update(error=exc.error_type,error_code=exc.code,folder=exc.folder,stage=exc.stage,attempt=exc.attempt)
        else:
            value.update(error_code='check_failed',stage='setup_or_store')
            record({'at':time.time(),'state':'error','stage':'setup_or_store','code':'check_failed','error_type':type(exc).__name__})
    value['finished_at']=time.time()
    activity(root,'inbox_complete',value['finished_at'],'idle')
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value),encoding='utf-8');tmp.chmod(0o600);tmp.replace(path)
    wake()
    return value


def wake():
    """Ask the existing root coordinator to run its fixed notification unit."""
    import socket
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(6);client.connect('/run/iva-browser/control.sock')
            client.sendall(b'{"action":"threads-notify"}\n');client.recv(1024)
    except OSError:pass # Existing minute timer is the durable fallback.
