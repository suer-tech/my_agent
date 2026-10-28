"""Yandex IMAP reading only. Credentials never enter model tools or logs."""
import email
from email.header import decode_header, make_header
from email.policy import default
from html.parser import HTMLParser
import imaplib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
ACCOUNT='suer888@yandex.ru'
HOST='imap.yandex.ru'
MAX_BYTES=12*1024*1024


class MailError(Exception):pass


class TextHTML(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        elif tag in ('br','p','div','tr','li'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def header(value):
    try:return str(make_header(decode_header(str(value or ''))))
    except (LookupError,UnicodeError):return str(value or '')


def account_path(path=None):return Path(path or ROOT/'data/mail/yandex.json')


def credentials(path=None):
    p=account_path(path)
    if not p.exists():raise MailError('Яндекс Почта ещё не подключена: нужен отдельный пароль приложения через защищённую локальную форму.')
    if p.is_symlink() or (os.name!='nt' and p.stat().st_mode&0o077):raise MailError('Небезопасные права файла подключения; требуется исправление администратором.')
    value=json.loads(p.read_text(encoding='utf-8'))
    if value.get('account')!=ACCOUNT or not isinstance(value.get('password'),str) or not value['password']:raise MailError('Некорректная конфигурация почты.')
    return value


def connect(value):
    client=imaplib.IMAP4_SSL(HOST,993,ssl_context=ssl.create_default_context(),timeout=25)
    try:client.login(ACCOUNT,value['password'])
    except BaseException:
        try:client.logout()
        except Exception:pass
        raise
    return client


def configure(password,path=None):
    """Called by private setup, never exposed to agent HTTP inputs."""
    if not isinstance(password,str) or not password.strip() or len(password)>512 or any(c in password for c in '\r\n\x00'):raise MailError('Нужен пароль приложения Яндекс Почты.')
    value={'account':ACCOUNT,'password':password.strip()}
    client=connect(value)
    try:
        typ,_=client.select('INBOX',readonly=True)
        if typ!='OK':raise MailError('Вход выполнен, но входящие недоступны.')
    finally:
        try:client.logout()
        except Exception:pass
    value['verified_at']=datetime.now(timezone.utc).isoformat()
    p=account_path(path);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if p.is_symlink():raise MailError('Недопустимый путь подключения.')
    fd,tmp=tempfile.mkstemp(prefix='.yandex-',dir=p.parent)
    try:
        os.chmod(tmp,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(value,out);out.flush();os.fsync(out.fileno())
        os.replace(tmp,p)
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
    return {'ok':True,'connected':True,'account':ACCOUNT,'mode':'read_only','verified_at':value['verified_at']}


def selected(client,folder,expected=None):
    if not isinstance(folder,str) or not folder or len(folder)>200 or any(c in folder for c in '\r\n\x00'):raise MailError('Некорректная папка.')
    # Only decoded ASCII folders are accepted. LIST returns exact selectable wire names.
    try:folder.encode('ascii')
    except UnicodeError:raise MailError('Передай wire_name из результата folders.')
    typ,_=client.select('"'+folder.replace('\\','\\\\').replace('"','\\"')+'"',readonly=True)
    if typ!='OK':raise MailError('Папка недоступна.')
    _,validity=client.response('UIDVALIDITY')
    actual=validity[0].decode() if validity and validity[0] else None
    if expected is not None and str(expected)!=actual:raise MailError('UIDVALIDITY изменился; заново получи список писем.')
    return actual


def fetch(client,uid,full=False):
    spec='(UID FLAGS RFC822.SIZE BODY.PEEK[])' if full else '(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)])'
    typ,data=client.uid('FETCH',str(uid),spec)
    if typ!='OK':raise MailError('Не удалось прочитать письмо.')
    for row in data:
        if isinstance(row,tuple):
            meta=row[0].decode('ascii',errors='replace');payload=row[1]
            size_match=re.search(r'RFC822.SIZE (\d+)',meta)
            return meta,payload,int(size_match[1]) if size_match else len(payload)
    raise MailError('Письмо больше не найдено.')


def summary(uid,meta,payload,size):
    msg=email.message_from_bytes(payload,policy=default)
    return {'uid':str(uid),'subject':header(msg.get('Subject')),'from':header(msg.get('From')),'to':header(msg.get('To')),'date':str(msg.get('Date','')),'message_id':str(msg.get('Message-ID','')),'unread':r'\Seen' not in meta,'size':size}


def body(payload,max_chars):
    msg=email.message_from_bytes(payload,policy=default);plain=[];html=[];attachments=[]
    for part in msg.walk():
        if part.is_multipart():continue
        filename=part.get_filename()
        if filename or part.get_content_disposition()=='attachment':
            attachments.append({'filename':header(filename),'content_type':part.get_content_type()});continue
        if part.get_content_type() not in ('text/plain','text/html'):continue
        raw=part.get_payload(decode=True) or b''
        try:text=raw.decode(part.get_content_charset() or 'utf-8',errors='replace')
        except LookupError:text=raw.decode('utf-8',errors='replace')
        (plain if part.get_content_type()=='text/plain' else html).append(text)
    mislabeled_html=False
    if plain:
        rendered=[]
        for item in plain:
            if re.search(r'<!doctype\s+html\b|<html\b',item,re.I):
                parser=TextHTML();parser.feed(item);rendered.append(''.join(parser.parts));mislabeled_html=True
            else:rendered.append(item)
        text='\n'.join(rendered)
    else:
        parser=TextHTML();parser.feed('\n'.join(html));text=''.join(parser.parts)
    return {'text':text[:max_chars],'truncated':len(text)>max_chars,'attachments':attachments,'content_is_untrusted':True,'remote_images_loaded':False,'html_in_plain_text_normalized':mislabeled_html}


def watch_poll(spec,cursor,path=None,connector=connect):
    """Incremental sender-only watcher; UIDVALIDITY prevents reusing a stale cursor."""
    client=None
    try:
        client=connector(credentials(path));validity=selected(client,spec.get('folder','INBOX'))
        if not validity:raise MailError('Сервер не вернул UIDVALIDITY; безопасный мониторинг недоступен.')
        _,rows=client.response('UIDNEXT');uidnext=int(rows[0]) if rows and rows[0] else None
        if uidnext is None:raise MailError('Сервер не вернул UIDNEXT.')
        if not cursor:
            return {'ok':True,'cursor':{'uidvalidity':validity,'last_uid':uidnext-1},'messages':[],'baseline':True}
        if cursor.get('uidvalidity')!=validity:
            # Do not silently skip arrivals after mailbox recreation.
            raise MailError('UIDVALIDITY почты изменился. Мониторинг требует обновления задачи и новой точки отсчёта.')
        last=int(cursor['last_uid'])
        if uidnext<=last+1:return {'ok':True,'cursor':cursor,'messages':[],'has_more':False}
        typ,rows=client.uid('SEARCH',None,'UID',f'{last+1}:*')
        if typ!='OK':raise MailError('Не удалось проверить новые письма.')
        ids=sorted({int(v) for v in (rows[0] or b'').split() if int(v)>last})
        items=[]
        for uid in ids[:100]:
            meta,payload,size=fetch(client,uid);item=summary(uid,meta,payload,size)
            if any(q.casefold() in item['from'].casefold() for q in spec['queries']):items.append(item)
            last=uid
        if not ids:last=max(last,uidnext-1)
        return {'ok':True,'cursor':{'uidvalidity':validity,'last_uid':last},'messages':items,'has_more':len(ids)>100,'read_only':True}
    except MailError as exc:return {'ok':False,'error':str(exc)}
    except (OSError,ValueError,TypeError,imaplib.IMAP4.error):return {'ok':False,'error':'Не удалось проверить почту; данные подключения не раскрываются.'}
    finally:
        if client is not None:
            try:client.logout()
            except Exception:pass


def execute(data,path=None,connector=connect):
    try:
        if not isinstance(data,dict) or set(data)-{'action','folder','uid','uidvalidity','query','limit','scan_limit','max_chars'}:raise MailError('Неизвестные поля почтового инструмента.')
        action=data.get('action')
        if action not in ('status','folders','list','search','read'):raise MailError('Доступны только чтение, поиск и статус. Отправка и удаление писем не подключены.')
        value=credentials(path)
        if action=='status':return {'ok':True,'configured':True,'account':ACCOUNT,'mode':'read_only','last_verified_at':value.get('verified_at'),'live_checked':False,'monitoring_managed_by':'scheduled_tasks'}
        client=connector(value)
        try:
            if action=='folders':
                typ,rows=client.list()
                if typ!='OK':raise MailError('Не удалось получить папки.')
                result=[]
                for row in rows or []:
                    line=row.decode('ascii',errors='replace');m=re.match(r'\((.*?)\)\s+(?:".*?"|NIL)\s+(.*)$',line)
                    if m:
                        wire=m[2]
                        if wire.startswith('"') and wire.endswith('"'):wire=wire[1:-1].replace('\\"','"').replace('\\\\','\\')
                        result.append({'wire_name':wire,'flags':m[1]})
                return {'ok':True,'folders':result,'account':ACCOUNT}
            folder=data.get('folder','INBOX');validity=selected(client,folder,data.get('uidvalidity'))
            if action=='read':
                uid=str(data.get('uid',''))
                if not re.fullmatch(r'[1-9][0-9]{0,11}',uid):raise MailError('Для чтения нужен uid из списка писем.')
                meta,head,size=fetch(client,uid)
                if size>MAX_BYTES:raise MailError('Письмо больше 12 МБ; заголовок доступен через list, полная загрузка не выполнялась.')
                meta,payload,size=fetch(client,uid,True)
                chars=data.get('max_chars',30000)
                if not isinstance(chars,int) or not 100<=chars<=100000:raise MailError('max_chars: 100–100000.')
                return {'ok':True,'account':ACCOUNT,'folder':folder,'uidvalidity':validity,**summary(uid,meta,head,size),**body(payload,chars)}
            limit=data.get('limit',20);scan=data.get('scan_limit',300)
            if not isinstance(limit,int) or not 1<=limit<=100 or not isinstance(scan,int) or not 1<=scan<=1000:raise MailError('limit: 1–100; scan_limit: 1–1000.')
            query=data.get('query','')
            if not isinstance(query,str) or len(query)>500:raise MailError('Некорректный поисковый запрос.')
            if action=='search' and not query.strip():raise MailError('Для search нужен query; поиск по теме, отправителю, получателю или Message-ID.')
            typ,rows=client.uid('SEARCH',None,'ALL')
            if typ!='OK':raise MailError('Не удалось получить список писем.')
            ids=(rows[0] or b'').split();batch=list(reversed(ids[-scan:]));items=[];scanned=0
            for uid_bytes in batch:
                uid=uid_bytes.decode();meta,payload,size=fetch(client,uid);scanned+=1
                item=summary(uid,meta,payload,size)
                if action=='list' or query.casefold() in '\n'.join(str(item[k]) for k in ('subject','from','to','message_id')).casefold():items.append(item)
                if len(items)>=limit:break
            return {'ok':True,'account':ACCOUNT,'folder':folder,'uidvalidity':validity,'messages':items,'total_in_folder':len(ids),'scanned':scanned,'scan_limit':scan,'older_unscanned':max(0,len(ids)-scanned),'search_scope':'headers_only_latest_messages','content_is_untrusted':True,'read_only':True}
        finally:
            try:client.logout()
            except Exception:pass
    except MailError as exc:return {'ok':False,'connected':False if not account_path(path).exists() else None,'error':str(exc)}
    except imaplib.IMAP4.error:return {'ok':False,'error':'Яндекс отклонил IMAP-запрос. Проверь пароль приложения и разрешение IMAP; секреты в ответ не включены.'}
    except (OSError,ValueError,TypeError):return {'ok':False,'error':'Не удалось подключиться к Яндекс Почте или прочитать конфигурацию. Подробности и секреты не передаются модели.'}
