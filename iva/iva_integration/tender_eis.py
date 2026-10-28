"""Bounded, read-only EIS retrieval. Selectors adapted from suer-tech/tender_agent_system
core/sources/zakupki_playwright.py at 513ebd38e335ed7e263d213fe365e837e56d0aee.
We preserve failure vs empty result, pagination and source evidence.
"""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlencode,urljoin,urlsplit,parse_qs
import urllib.request
from bs4 import BeautifulSoup
from iva_integration.web_browser import public_url

ROOT=Path(__file__).resolve().parents[1]
BASE='https://zakupki.gov.ru'
CA=ROOT/'data/eis-ca.pem'
_lock=threading.Lock()
_last=0.


def valid_url(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('zakupki.gov.ru','www.zakupki.gov.ru') or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Разрешены только публичные HTTPS-страницы ЕИС')
    public_url(url)
    return url


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        valid_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch(url):
    global _last
    valid_url(url)
    context=ssl.create_default_context()
    if CA.exists():context.load_verify_locations(str(CA))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect(),urllib.request.HTTPSHandler(context=context))
    with _lock:
        delay=max(0,1-(time.monotonic()-_last))
        if delay:time.sleep(delay)
        _last=time.monotonic()
    request=urllib.request.Request(url,headers={
        'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Accept-Language':'ru-RU,ru;q=0.9','Accept':'text/html,application/xhtml+xml'})
    with opener.open(request,timeout=22) as response:
        mime=response.headers.get_content_type()
        content=response.read(8_000_001)
        if len(content)>8_000_000:raise ValueError('Документ превышает лимит 8 МБ')
        if mime not in ('text/html','application/xhtml+xml','text/plain'):
            return content,response.url
        text=content.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
        return text,response.url


def parse_document(content,url):
    """Bounded text extraction; never execute macros, embedded links or instructions."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    pages=[]
    if content.startswith(b'%PDF'):
        import pymupdf
        with pymupdf.open(stream=content,filetype='pdf') as document:
            if document.needs_pass:raise ValueError('Документ защищён паролем')
            if len(document)>80:raise ValueError('Больше 80 страниц: требуется отдельный разбор документа')
            for i,page in enumerate(document):
                pages.append({'page':i+1,'text':page.get_text(sort=True)[:10000]})
    elif content.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(content)) as document:
            if 'word/document.xml' not in document.namelist():
                raise ValueError('Архив/таблица: автоматический разбор этого формата пока не подключён')
            info=document.getinfo('word/document.xml')
            if info.file_size>4_000_000:raise ValueError('Слишком большой распакованный документ')
            xml=document.read(info)
            if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:raise ValueError('XML-сущности запрещены')
            root=ET.fromstring(xml)
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            for index,paragraph in enumerate(root.findall('.//w:p',ns)):
                text=''.join(node.text or '' for node in paragraph.findall('.//w:t',ns))
                if text:pages.append({'paragraph':index+1,'text':text[:10000]})
    else:raise ValueError('Формат вложения не поддержан: нельзя утверждать, что условия проверены')
    selected=[];length=0
    for page in pages:
        if length+len(page['text'])>45000:break
        selected.append(page);length+=len(page['text'])
    return {'ok':True,'url':url,'pages':selected,'text_truncated':len(selected)<len(pages),
            'needs_ocr':length<50,'sha256':hashlib.sha256(content).hexdigest(),
            'checked_at':datetime.now(timezone.utc).isoformat(),'untrusted_source':True}


def parse_page(html,url):
    soup=BeautifulSoup(html,'html.parser')
    for tag in soup(['script','style','noscript']):tag.decompose()
    text=soup.get_text(' ',strip=True)
    cards=[]
    for card in soup.select('.search-registry-entry-block'):
        number=next((a for a in card.select('a[href*="regNumber="]') if re.search(r'\d{10,}',a.get_text())),None)
        number=number or card.select_one('.registry-entry__header-mid__number a, .cardMainInfo__purchaseLink a')
        if not number:continue
        link=urljoin(BASE,number.get('href',''))
        title=card.select_one('.registry-entry__body-value')
        price=card.select_one('.price-block__value')
        customer=card.select_one('.registry-entry__body-href a')
        raw=card.get_text(' ',strip=True)
        deadline=re.search(r'Окончание подачи заявок\s*(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}))?',raw,re.I)
        today=datetime.now(timezone(timedelta(hours=5))).date()
        expired=False
        if deadline:
            try:expired=datetime.strptime(deadline[1],'%d.%m.%Y').date()<today
            except ValueError:pass
        cards.append({'number':number.get_text(' ',strip=True),'url':link,
                      'title':title.get_text(' ',strip=True) if title else None,
                      'price_text':price.get_text(' ',strip=True) if price else None,
                      'customer':customer.get_text(' ',strip=True) if customer else None,
                      'deadline_text':deadline[0] if deadline else None,'expired_by_date':expired,
                      'evidence_text':raw[:6000]})
    links=[];seen=set()
    for anchor in soup.select('a[href]'):
        link=urljoin(url,anchor['href'])
        if urlsplit(link).hostname not in ('zakupki.gov.ru','www.zakupki.gov.ru') or link in seen:continue
        seen.add(link);links.append({'title':anchor.get_text(' ',strip=True)[:180],'url':link})
    # Navigation links must not crowd out protocols, print forms and contract records.
    links.sort(key=lambda x:not bool(re.search(r'protocol|contract|complaint|regNumber|printForm|document',x['url'],re.I)))
    blocked=bool(re.search(r'доступ (?:ограничен|заблокирован)|access denied|подтвердите.{0,20}человек|captcha',text[:2500],re.I))
    pagination=pagination_info(soup,url,len(cards))
    return {'ok':not blocked,'access_challenge':blocked,'url':url,'pagination':pagination,
            'title':soup.title.get_text() if soup.title else '',
            'cards':cards,'text':text[:28000],'text_truncated':len(text)>28000,
            'links':links[:100],'checked_at':datetime.now(timezone.utc).isoformat(),
            'untrusted_source':True,'not_found_verified':not cards and (pagination['total_results']==0 or bool(re.search(r'поиск не дал результатов|ничего не найдено|записей не найдено|найдено.{0,5}0 запис',text,re.I)))}


def pagination_info(soup,url,card_count):
    """Only observed navigation/total counts prove the end; unknown is not empty."""
    page=int(parse_qs(urlsplit(url).query).get('pageNumber',['1'])[0])
    nav=soup.select('.paginator, .pagination, [class*=pagination]')
    numbers=[]
    for block in nav:
        for link in block.select('a[href]'):
            values=parse_qs(urlsplit(link['href']).query).get('pageNumber',[])
            if values and values[0].isdigit():numbers.append(int(values[0]))
    total=None
    count=soup.select_one('.search-results__total')
    if count:
        digits=re.search(r'\d[\d\s\xa0]*',count.get_text(' ',strip=True))
        if digits:total=int(re.sub(r'\D','',digits[0]) or '0')
    next_pages=[n for n in numbers if n>page]
    # No navigation on a nonempty single page is an observed end. A full page
    # without usable navigation remains unknown and is followed by a probe.
    complete=bool(nav and not next_pages and card_count<20)
    if total is not None:complete=page*20>=total
    return {'page':page,'next_page':min(next_pages) if next_pages else None,
            'total_results':total,'end_verified':complete,'navigation_present':bool(nav)}


def search_url(query,mode='active',page=1,price_min=None,price_max=None,published_from=None,published_to=None):
    if not isinstance(query,str) or not 1<=len(query.strip())<=250:raise ValueError('Нужна строка поиска 1..250 символов')
    if mode not in ('active','history','contracts','contracts223','complaints') or type(page)!=int or page<1:raise ValueError('Неверный режим или страница (нужно положительное целое число)')
    params={'searchString':query,'morphology':'on','pageNumber':page,'recordsPerPage':'_20','sortDirection':'false'}
    paths={'active':'/epz/order/extendedsearch/results.html','history':'/epz/order/extendedsearch/results.html',
           'contracts':'/epz/contract/search/results.html','contracts223':'/epz/contractfz223/search/results.html','complaints':'/epz/complaint/search/results.html'}
    if mode in ('active','history'):
        params.update(fz44='on',fz223='on',sortBy='UPDATE_DATE')
        if mode=='active':
            params['af']='on'
            params['applSubmissionCloseDateFrom']=datetime.now(timezone(timedelta(hours=5))).strftime('%d.%m.%Y')
        else:params.update(pc='on')
        if price_min is not None:params['priceFromGeneral']=float(price_min)
        if price_max is not None:params['priceToGeneral']=float(price_max)
        if price_min is not None or price_max is not None:params['currencyIdGeneral']='-1'
        for name,value in [('publishDateFrom',published_from),('publishDateTo',published_to)]:
            if value is not None:
                datetime.strptime(value,'%d.%m.%Y')
                params[name]=value
    return BASE+paths[mode]+'?'+urlencode(params)


class Store:
    def __init__(self,path=None):
        self.db=sqlite3.connect(path or ROOT/'data/tender-agent.sqlite3',timeout=20)
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tender_pages(url TEXT NOT NULL,checked_at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(url,checked_at));
        CREATE TABLE IF NOT EXISTS tender_profile(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tender_feedback(source TEXT PRIMARY KEY,payload TEXT NOT NULL,owner_quote TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tender_runs(source TEXT PRIMARY KEY,state TEXT NOT NULL,result TEXT);
        """)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO tender_profile VALUES(1,?)',((ROOT/'iva_integration/tender_profile.json').read_text(encoding='utf-8'),))
    def close(self):self.db.close()
    def profile(self):
        return {'profile':json.loads(self.db.execute('SELECT payload FROM tender_profile WHERE id=1').fetchone()[0]),
                'preferences':[json.loads(r[0]) for r in self.db.execute("SELECT payload FROM tender_feedback WHERE json_extract(payload,'$.scope')='preference' ORDER BY created DESC")],
                'feedback':[json.loads(r[0]) for r in self.db.execute("SELECT payload FROM tender_feedback WHERE json_extract(payload,'$.scope')='one_tender' ORDER BY created DESC LIMIT 40")]}
    def feedback(self,source,body,evidence,note,scope='one_tender',tender_url=None):
        if not source or not body or not evidence or evidence not in body:raise PermissionError('Нужна точная цитата текущего владельца')
        if scope not in ('one_tender','preference'):raise ValueError('Обратная связь относится к одному тендеру либо явному постоянному предпочтению')
        if not note.strip() or len(note)>3000:raise ValueError('Нужна краткая причина')
        payload={'note':note,'scope':scope,'tender_url':tender_url,'owner_quote':evidence}
        source=source+':'+hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        with self.db:
            existing=self.db.execute('SELECT payload FROM tender_feedback WHERE source=?',(source,)).fetchone()
            if existing:return {'saved':True,'duplicate':True,'feedback':json.loads(existing[0])}
            self.db.execute('INSERT INTO tender_feedback VALUES(?,?,?,?)',(source,json.dumps(payload,ensure_ascii=False),evidence,datetime.now(timezone.utc).isoformat()))
        return {'saved':True,'feedback':payload}
    def read(self,url,refresh=True):
        valid_url(url)
        if not refresh:
            row=self.db.execute('SELECT payload FROM tender_pages WHERE url=? ORDER BY checked_at DESC LIMIT 1',(url,)).fetchone()
            if row:return {**json.loads(row[0]),'cached':True,'must_refresh_before_bid':True}
        try:
            html,final=fetch(url)
            result=parse_document(html,final) if isinstance(html,bytes) else parse_page(html,final)
        except Exception as error:
            # Errors are never interpreted as "no matching procurement".
            return {'ok':False,'url':url,'error':str(error)[:300] if isinstance(error,ValueError) else type(error).__name__,
                    'http_status':getattr(error,'code',None),'checked_at':datetime.now(timezone.utc).isoformat(),'not_found_verified':False}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO tender_pages VALUES(?,?,?)',(url,result['checked_at'],json.dumps(result,ensure_ascii=False)))
            self.db.execute("DELETE FROM tender_pages WHERE checked_at < datetime('now','-90 days')")
        return result
