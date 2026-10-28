"""Private, bounded DOM commands inside the single server browser owner."""
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit,quote

ROOT=Path('/opt/iva/data/threads-browser')


def allowed_url(url):
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in ('www.threads.com','threads.com') or u.username or u.password or u.port:
        raise ValueError('Threads URL required')
    if u.query or u.fragment or (u.path!='/' and not re.fullmatch(r'/@[\w.]+/post/[\w-]+/?',u.path)):
        raise ValueError('Only home and public post URLs allowed')
    return url


def snapshot(page):
    result=page.evaluate('''() => {
      const visible=e=>!!(e.getClientRects().length && getComputedStyle(e).visibility!=='hidden');
      const links=[...document.querySelectorAll('a[href]')].filter(visible).map(a=>{
        let root=a; for(let i=0;i<7 && root.parentElement;i++){
          if(root.getBoundingClientRect().height>160)break;root=root.parentElement;
        }
        return {url:a.href,text:a.innerText,context:root.innerText.slice(0,2500)};
      }).filter(a=>a.url.includes('/post/')).slice(0,70);
      return {url:location.origin+location.pathname,search_url:location.pathname==='/search'?location.href:null,search_links:[...document.querySelectorAll('a[href*="/search"]')].map(a=>({text:a.innerText,url:a.href})).slice(0,12),text:document.body.innerText.slice(0,26000),links,
        buttons:[...document.querySelectorAll('button,[role="button"]')].filter(visible).map(e=>({role:e.getAttribute('role')||'button',text:e.innerText,label:e.getAttribute('aria-label')})).slice(0,65),
        icons:[...document.querySelectorAll('svg[aria-label]')].filter(visible).map(e=>({label:e.getAttribute('aria-label'),buttonText:e.closest('button,[role=button]')?.innerText})).slice(0,60),
        editors:[...document.querySelectorAll('textarea,input,[contenteditable="true"]')].filter(visible).map(e=>({tag:e.tagName,role:e.getAttribute('role'),placeholder:e.getAttribute('placeholder'),label:e.getAttribute('aria-label'),testid:e.getAttribute('data-testid')}))};
    }''')
    from iva_integration.threads_hourly import candidate_cards
    result['cards']=candidate_cards(page)
    return result


def process_pending(context):
    request_path=ROOT/'rpc-request.json'
    if not request_path.exists(): return
    request=json.loads(request_path.read_text())
    response_path=ROOT/'rpc-response.json'
    if response_path.exists() and json.loads(response_path.read_text()).get('id')==request.get('id'):return
    db=sqlite3.connect(ROOT/'rpc.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,action TEXT,state TEXT,result TEXT)')
    existing=db.execute('SELECT state,result FROM commands WHERE id=?',(request['id'],)).fetchone()
    if existing:
        result=json.loads(existing[1]) if existing[1] else {'ok':False,'error':'Uncertain previous command; no replay'}
    else:
        with db:db.execute('INSERT INTO commands VALUES (?,?,?,NULL)',(request['id'],request['action'],'claimed'))
        try:
            pages=[p for p in context.pages if not p.is_closed() and urlsplit(p.url).hostname in ('www.threads.com','threads.com')]
            if not pages:raise RuntimeError('No Threads page')
            from iva_integration.threads_page import working_page
            page=working_page(context)
            action=request['action']
            if action=='open':
                page.goto(allowed_url(request['url']),wait_until='domcontentloaded',timeout=45000)
                page.wait_for_timeout(2500)
            elif action=='search':
                query=request.get('query','')
                if not isinstance(query,str) or not 2<=len(query)<=160:raise ValueError('Search query required')
                page.goto('https://www.threads.com/search?q='+quote(query)+'&filter=recent',wait_until='domcontentloaded',timeout=45000)
                page.wait_for_function("""() => [...document.querySelectorAll('a[href*=\"/post/\"] time[datetime]')].some(e=>e.getClientRects().length) || /No results|Nothing found|Ничего не найдено|Нет результатов/.test(document.body.innerText)""",timeout=30000)
                page.wait_for_timeout(700)
            elif action=='scroll':page.mouse.wheel(0,650);page.wait_for_timeout(1500)
            elif action=='click':
                if request.get('role') not in ('button','link'):raise ValueError('Bounded role required')
                target=page.get_by_role(request['role'],name=request['name'],exact=True)
                if target.count()!=1:raise ValueError('Target must be unique')
                target.click(timeout=12000);page.wait_for_timeout(1500)
            elif action=='fill':
                if not isinstance(request.get('text'),str) or not 1<=len(request['text'])<=500:raise ValueError('Text limit')
                # Role locators exclude aria-hidden background composers behind a modal.
                editors=page.get_by_role('textbox')
                shown=[x for x in editors.all() if x.is_visible()]
                if len(shown)!=1:raise ValueError('One visible reply editor required')
                shown[0].fill(request['text'])
            elif action=='preflight':
                from iva_integration.threads_hourly import publish,STORE
                url=allowed_url(request['url'])
                if '/post/' not in url:raise ValueError('Public thread required')
                original=page.url
                text='Я делаю расширение для откликов. Техническая проверка редактора без публикации. '+STORE
                class Prepared(Exception):pass
                def stop_before_submit():raise Prepared()
                prepared=False
                try:
                    page.goto(url,wait_until='domcontentloaded',timeout=45000)
                    page.wait_for_function("""path=>[...document.querySelectorAll('a[href]')].some(a=>new URL(a.href).pathname===path&&a.querySelector('time[datetime]'))""",arg=urlsplit(url).path,timeout=30000)
                    publish(page,url,text,before_submit=stop_before_submit)
                except Prepared:prepared=True
                except Exception:
                    data=snapshot(page)
                    diagnostic={k:data.get(k) for k in ('url','icons','buttons','editors')}
                    target=ROOT/'editor-diagnostics.json';target.write_text(json.dumps(diagnostic,ensure_ascii=False));target.chmod(0o600)
                    raise
                finally:
                    for editor in page.get_by_role('textbox').all():
                        if editor.is_visible() and editor.inner_text().strip()==text:editor.fill('')
                    page.goto(original,wait_until='domcontentloaded',timeout=45000)
                preflight_result={'verified':prepared,'submitted':False,'root_url':url}
            elif action=='inbox':
                from iva_integration.threads_inbox import check
                inbox_result=check(context,force=True)
            elif action!='snapshot':raise ValueError('Unknown command')
            if action=='preflight':result={'ok':preflight_result['verified'],'preflight':preflight_result}
            elif action=='inbox':result={'ok':inbox_result.get('state')=='ok','inbox':inbox_result}
            else:result={'ok':True,'snapshot':snapshot(page)}
        except Exception as exc:result={'ok':False,'error':type(exc).__name__+': '+str(exc)[:250]}
        with db:db.execute('UPDATE commands SET state=?,result=? WHERE id=?',('done',json.dumps(result),request['id']))
    db.close()
    temporary=response_path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'id':request['id'],**result},ensure_ascii=False));temporary.chmod(0o600);temporary.replace(response_path)
