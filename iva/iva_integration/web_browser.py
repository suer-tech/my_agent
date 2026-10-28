"""Isolated Playwright reader. No owner cookies, arbitrary JS, forms or purchases."""
from concurrent.futures import Future
from datetime import datetime, timezone
import ipaddress
import json
import queue
import socket
import threading
import time
from urllib.parse import urlsplit, quote_plus


def public_url(url, resolve=True):
    if not isinstance(url,str) or len(url)>4096:
        raise ValueError('Invalid URL')
    parsed=urlsplit(url)
    if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,80,443):
        raise ValueError('Only public HTTP(S) pages are supported')
    host=parsed.hostname.rstrip('.').lower()
    if host=='localhost' or host.endswith(('.localhost','.local','.internal')):
        raise ValueError('Local network addresses are unavailable')
    try:addresses=[ipaddress.ip_address(host)]
    except ValueError:
        if not resolve:return url
        addresses=[ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)]
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError('Local network addresses are unavailable')
    return url


def search_url(query,marketplace='web'):
    if not isinstance(query,str) or not 1<=len(query.strip())<=300:
        raise ValueError('Search query must contain 1–300 characters')
    q=quote_plus(query.strip())
    templates={'web':'https://www.bing.com/search?q=',
               'ozon':'https://www.ozon.ru/search/?text=',
               'wildberries':'https://www.wildberries.ru/catalog/0/search.aspx?search=',
               'yandex_market':'https://market.yandex.ru/search?text='}
    if marketplace not in templates:raise ValueError('Unknown marketplace')
    return templates[marketplace]+q


EXTRACT=r'''() => {
 const text=(document.querySelector('main')?.innerText||document.body?.innerText||'').slice(0,18000);
 const seen=new Set(), links=[];
 const anchors=[...document.querySelectorAll('a[href]')];
 const score=(a)=>/\/product\/|\/catalog\/\d+|\/product--|\/card\/|\/offer\//.test(a.href)?1:0;
 anchors.sort((a,b)=>score(b)-score(a));
 for(const a of anchors) {
  const title=(a.innerText||a.getAttribute('aria-label')||'').trim().replace(/\s+/g,' ').slice(0,180);
  if(!title||!/^https?:/.test(a.href)||seen.has(a.href))continue;
  seen.add(a.href);links.push({title,url:a.href});if(links.length===60)break;
 }
 const products=[];
 const walk=(x)=>{if(!x||typeof x!=='object'||products.length>=20)return;
  if(Array.isArray(x)){for(const y of x)walk(y);return;}
  if([x['@type']].flat().includes('Product'))products.push({name:x.name,sku:x.sku,brand:x.brand,offers:x.offers,url:x.url});
  if(x['@graph'])walk(x['@graph']);if(x.itemListElement)walk(x.itemListElement);if(x.item)walk(x.item);
 };
 for(const s of document.querySelectorAll('script[type="application/ld+json"]')){if(s.textContent.length>1000000)continue;try{walk(JSON.parse(s.textContent));}catch{}}
 return {title:document.title,text,links,products_json:JSON.stringify(products).slice(0,12000)};
}'''


class BrowserWorker:
    def __init__(self):
        self.queue=queue.Queue(maxsize=8)
        self.thread=threading.Thread(target=self._run,name='iva-playwright',daemon=True)
        self.thread.start()

    def request(self,data):
        action=data.get('action')
        if action not in ('open','search','read','scroll','close'):
            return {'ok':False,'error':'Unsupported browser action'}
        future=Future()
        try:self.queue.put_nowait((data,future))
        except queue.Full:return {'ok':False,'error':'Browser is busy; try later'}
        try:return future.result(timeout=55)
        except TimeoutError:
            future.cancel()
            return {'ok':False,'error':'Browser timed out. No purchase or financial write was requested.'}

    def _run(self):
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
        browser=context=page=driver=None
        dns_cache={}
        def guard(route):
            request=route.request
            try:
                # This reader never submits forms, carts or payment requests.
                if request.method not in ('GET','HEAD'):return route.abort()
                host=urlsplit(request.url).hostname
                if time.monotonic()-dns_cache.get(host,0)>60:
                    public_url(request.url);dns_cache[host]=time.monotonic()
                else:public_url(request.url,resolve=False)
                route.continue_()
            except Exception:route.abort()
        while True:
            data,future=self.queue.get()
            if future.cancelled():continue
            try:
                action=data['action']
                if action=='close':
                    if context:context.close()
                    context=page=None
                    result={'ok':True,'closed':True}
                else:
                    target=search_url(data.get('query'),data.get('marketplace','web')) if action=='search' else data.get('url')
                    if action=='open' and not target:raise ValueError('URL required')
                    if target:public_url(target)
                    if not driver:driver=sync_playwright().start()
                    if not browser or not browser.is_connected():
                        browser=driver.chromium.launch(headless=True)
                        context=page=None
                    if not context:
                        context=browser.new_context(locale='ru-RU',timezone_id='Asia/Yekaterinburg',accept_downloads=False,service_workers='block')
                        context.route('**/*',guard)
                        context.route_web_socket('**/*',lambda route:route.close())
                        page=context.new_page();page.set_default_timeout(12000)
                    response=None;timed_out=False
                    if target:
                        try:response=page.goto(target,wait_until='domcontentloaded',timeout=25000)
                        except PlaywrightTimeout:timed_out=True
                        page.wait_for_timeout(1800)
                    elif action=='scroll':
                        page.mouse.wheel(0,900);page.wait_for_timeout(900)
                    elif page.url=='about:blank':raise ValueError('Open a URL or search first')
                    result=page.evaluate(EXTRACT)
                    products=result.pop('products_json')
                    try:result['products']=json.loads(products)
                    except ValueError:result['products']=[]
                    result.update(ok=True,url=page.url,checked_at=datetime.now(timezone.utc).isoformat(),http_status=response.status if response else None,navigation_timed_out=timed_out,untrusted_web_content=True)
                    if navigation_failed(page.url,response.status if response else None):
                        result.update(ok=False,error='Browser navigation failed; not a successful page read')
                    lower=(result['title']+'\n'+result['text'][:2500]).lower()
                    result['access_challenge']=(response is not None and response.status in (401,403,429,498)) or '/sorry/' in page.url or any(s in lower for s in ('captcha','капч','подтвердите, что вы','доступ ограничен','доступ заблокирован','access denied','verify you are human','проверяем, что вы','подозрительная активность','подозрительный трафик','похоже, нет соединения'))
                    if action=='search' and data.get('marketplace','web')=='web':
                        result['search_results_visible']=page.locator('#b_results .b_algo').count()>0
                        if not result['search_results_visible']:
                            result['search_warning']='Search page loaded but no result cards were confirmed; do not invent results. A search API provider can be connected for reliable discovery.'
                    result['scope']='Anonymous public browsing. Prices/delivery may depend on region, variant, login and payment method. No orders or ledger changes.'
            except Exception as error:
                result={'ok':False,'error':str(error)[:250] if isinstance(error,ValueError) else type(error).__name__,'untrusted_web_content':True}
            if not future.cancelled():future.set_result(result)


_worker=None
_lock=threading.Lock()
def browser_request(data):
    global _worker
    with _lock:
        if _worker is None:_worker=BrowserWorker()
    return _worker.request(data)


def navigation_failed(url,status):
    return url.startswith(('chrome-error:','about:neterror','edge-error:')) or status is not None and status>=400
