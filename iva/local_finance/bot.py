import argparse
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime,timedelta,timezone

from .assistant import Assistant,Ledger,setup
from .codex_cli import Codex,CliError
from finance.delivery import deliver
from .voice import Voice,VoiceError,MAX_BYTES,MAX_SECONDS,ROOT
from .attachments import Attachments

OWNER = 412850740


def best_photo(sizes):return max(sizes,key=lambda p:(int(p.get('width',0))*int(p.get('height',0)),int(p.get('file_size',0))))


class TelegramFatal(RuntimeError): pass


def load_env(path):
    if not path.exists(): return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key,sep,value = line.partition('=')
        if not sep: raise ValueError('Некорректная строка .env')
        os.environ[key.strip()] = value.strip()


class Telegram:
    def __init__(self,token):
        self.token = token
        proxy=os.environ.get('TELEGRAM_HTTPS_PROXY','').strip()
        self._opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy})) if proxy else None

    def open(self,request,timeout=45):
        return self._opener.open(request,timeout=timeout) if self._opener else urllib.request.urlopen(request,timeout=timeout)

    def call(self,method,payload):
        return self.request(method,json.dumps(payload).encode(),'application/json')

    def request(self,method,body,content_type,timeout=45):
        try:
            req = urllib.request.Request('https://api.telegram.org/bot'+self.token+'/'+method,data=body,headers={'Content-Type':content_type})
            with self.open(req,timeout=timeout) as response: value = json.load(response)
            if not value.get('ok'): raise ValueError()
            return value['result']
        except urllib.error.HTTPError as exc:
            if exc.code==401: raise TelegramFatal('Telegram отклонил локальный токен; настройте перевыпущенный токен') from None
            if exc.code==409: raise TelegramFatal('Конфликт Telegram polling: остановите Ива/другую копию с этим токеном') from None
            raise RuntimeError('Telegram API временно недоступен; проверьте сеть') from None
        except Exception: raise RuntimeError('Telegram API недоступен; проверьте сеть и локальный токен') from None

    def send(self,chat,text):
        # Keep a single outgoing response: uncertain failure cannot cause partial multipart retry.
        if len(text)>3900: text = text[:3800]+'\n[Ответ сокращён; уточните период/запрос.]'
        payload={'chat_id':chat,'text':text}
        if getattr(self,'reply_keyboard',None):payload['reply_markup']=self.reply_keyboard
        return self.call('sendMessage',payload)

    def send_progress(self,chat,text):
        return self.request('sendMessage',json.dumps({'chat_id':chat,'text':text}).encode(),'application/json',timeout=4)

    def typing(self,chat):
        return self.request('sendChatAction',json.dumps({'chat_id':chat,'action':'typing'}).encode(),'application/json',timeout=2)

    def send_voice(self,chat,content):
        if chat!=OWNER or not content.startswith(b'OggS') or b'OpusHead' not in content[:512] or len(content)>MAX_BYTES:
            raise VoiceError('Некорректный адресат или формат голосового ответа')
        boundary = uuid.uuid4().hex
        markup=(f'--{boundary}\r\nContent-Disposition: form-data; name="reply_markup"\r\n\r\n'+json.dumps(self.reply_keyboard,ensure_ascii=False)+'\r\n').encode() if getattr(self,'reply_keyboard',None) else b''
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{OWNER}\r\n'
                ).encode()+markup+(f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; filename="reply.ogg"\r\nContent-Type: audio/ogg\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
        return self.request('sendVoice',body,f'multipart/form-data; boundary={boundary}')

    def send_document(self,chat,path,caption=''):
        if chat!=OWNER:raise PermissionError('Only owner')
        path=Path(path).resolve()
        boundary=uuid.uuid4().hex
        content=path.read_bytes()
        if len(content)>20*1024*1024:raise ValueError('Отчёт больше 20 MiB; доступен через инструмент report')
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{OWNER}\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption[:900]}\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="tender-report.md"\r\nContent-Type: text/markdown; charset=utf-8\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
        return self.request('sendDocument',body,f'multipart/form-data; boundary={boundary}')

    def download_voice(self,metadata,path):
        if int(metadata.get('file_size') or 0)>MAX_BYTES or int(metadata.get('duration') or 0)>MAX_SECONDS:
            raise VoiceError('Запись должна быть не больше 12 MiB и 3 минут.')
        record = self.call('getFile',{'file_id':metadata['file_id']})
        remote = record.get('file_path','')
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',remote) or remote.startswith('/') or '..' in PurePosixPath(remote).parts:
            raise VoiceError('Telegram вернул некорректный путь файла')
        if int(record.get('file_size') or 0)>MAX_BYTES: raise VoiceError('Аудиофайл слишком большой')
        path.parent.mkdir(parents=True,exist_ok=True)
        part = path.with_suffix('.part')
        try:
            with self.open('https://api.telegram.org/file/bot'+self.token+'/'+remote,timeout=45) as response:
                content = response.read(MAX_BYTES+1)
            if len(content)>MAX_BYTES: raise VoiceError('Аудиофайл слишком большой')
            part.write_bytes(content);part.replace(path)
        except VoiceError: raise
        except Exception: raise VoiceError('Не удалось скачать голосовое из Telegram. Попробуйте позже; запись остаётся в реестре.') from None
        finally: part.unlink(missing_ok=True)
        return path

    def download_attachment(self,metadata,path):
        limit=20*1024*1024
        if int(metadata.get('file_size') or 0)>limit:raise RuntimeError('Вложение больше 20 MiB. Разделите документ.')
        record=self.call('getFile',{'file_id':metadata['file_id']})
        remote=record.get('file_path','')
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',remote) or remote.startswith('/') or '..' in PurePosixPath(remote).parts:raise RuntimeError('Некорректный путь Telegram-файла')
        if int(record.get('file_size') or 0)>limit:raise RuntimeError('Вложение слишком большое')
        path.parent.mkdir(parents=True,exist_ok=True)
        part=path.with_suffix('.part')
        try:
            with self.open('https://api.telegram.org/file/bot'+self.token+'/'+remote,timeout=45) as response:content=response.read(limit+1)
            if len(content)>limit:raise RuntimeError('Вложение больше 20 MiB')
            part.write_bytes(content);part.replace(path)
        except Exception:raise RuntimeError('Не удалось скачать вложение; ссылка сохранена для повтора') from None
        finally:part.unlink(missing_ok=True)
        return path


class Bot:
    def __init__(self,ledger,assistant,telegram,voice=None):
        self.l,self.assistant,self.telegram = ledger,assistant,telegram
        engine = voice or Voice()
        self.voice = engine if engine.ready else None
        self.voice_only=False
        self.next_reminder_check = 0
        setup(ledger.db)
        self.attachments=Attachments(ledger,assistant,telegram)
        if not ledger.rows("SELECT value FROM local_meta WHERE key='initial_pdf_binding_loaded'"):
            with ledger.transaction():
                hint=os.environ.get('FINANCE_INITIAL_PDF_LOAN','')
                if hint:ledger.db.execute("INSERT OR REPLACE INTO local_meta VALUES('next_pdf_loan',?)",(hint,))
                ledger.db.execute("INSERT INTO local_meta VALUES('initial_pdf_binding_loaded','yes')")

    def check_reminders(self):
        now = datetime.now(timezone(timedelta(hours=5)))
        hour = int(os.environ.get('FINANCE_REMINDER_HOUR','9'))
        if not 0<=hour<=23: raise ValueError('FINANCE_REMINDER_HOUR: 0..23')
        if now.hour<hour or time.monotonic()<self.next_reminder_check: return
        self.next_reminder_check = time.monotonic()+60
        result = deliver(self.l,now.date().isoformat(),(now.date()+timedelta(days=366)).isoformat(),lambda text:self.telegram.send(OWNER,text))
        if any(r['state']=='uncertain' for r in result):
            print('Доставка напоминания не подтверждена; требуется сверка с чатом.',flush=True)

    def ingest(self,updates):
        with self.l.db:
            old = self.l.rows("SELECT value FROM local_meta WHERE key='offset'")
            offset = int(old[0]['value']) if old else 0
            for update in updates:
                uid = update['update_id']
                offset = max(offset,uid+1)
                msg = update.get('message',{})
                chat,user = msg.get('chat',{}),msg.get('from',{})
                if chat.get('type')=='private' and chat.get('id')==OWNER and user.get('id')==OWNER:
                    self.l.db.execute('INSERT OR IGNORE INTO local_telegram_messages VALUES(?,?,?)',(uid,msg.get('message_id'),json.dumps(msg,ensure_ascii=False)))
                    text = msg.get('text')
                    metadata = None
                    audio = msg.get('voice') or msg.get('audio')
                    if audio:
                        text = '/incoming_voice'
                        metadata = json.dumps({key:audio.get(key) for key in ('file_id','file_unique_id','duration','file_size')})
                    document=msg.get('document',{})
                    if document and (document.get('mime_type')=='application/pdf' or str(document.get('file_name','')).lower().endswith('.pdf')):
                        text='/incoming_pdf'
                        metadata=json.dumps({key:document.get(key) for key in ('file_id','file_unique_id','file_size','file_name')})
                    elif document:
                        suffix=Path(str(document.get('file_name',''))).suffix.lower()
                        kind='image' if suffix in ('.jpg','.jpeg','.png') else 'text' if suffix in ('.txt','.csv','.tsv','.md','.log') or document.get('mime_type','').startswith('text/') else 'unsupported'
                        text='/incoming_'+kind
                        metadata=json.dumps({**{key:document.get(key) for key in ('file_id','file_unique_id','file_size','file_name')},'caption':msg.get('caption',''),'kind':kind})
                    if msg.get('photo'):
                        photo=best_photo(msg['photo'])
                        text='/incoming_image'
                        metadata=json.dumps({**{key:photo.get(key) for key in ('file_id','file_unique_id','file_size')},'caption':msg.get('caption',''),'kind':'image'})
                    if text=='/incoming_pdf':
                        details=json.loads(metadata);details.update(caption=msg.get('caption',''),kind='pdf')
                        metadata=json.dumps(details)
                    if text=='/incoming_image':
                        details=json.loads(metadata);details.update(media_group_id=msg.get('media_group_id'),received_at=time.time())
                        metadata=json.dumps(details)
                    if text is None: text = '/unsupported_media'
                    self.l.db.execute('INSERT OR IGNORE INTO local_updates(id,chat,text,metadata) VALUES(?,?,?,?)',(uid,OWNER,text,metadata))
            self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('offset',?)",(str(offset),))

    def process_one(self):
        pending = self.l.rows("SELECT * FROM local_updates WHERE state IN ('pending','ready') AND (text!='/incoming_voice' OR ?) ORDER BY id LIMIT 1",(self.voice is not None,))
        if not pending: return False
        update = pending[0]
        metadata = json.loads(update.get('metadata') or '{}')
        if update['state']=='pending' and update['text']=='/incoming_image' and metadata.get('media_group_id'):
            group=[]
            for row in self.l.rows('SELECT * FROM local_updates WHERE text=? ORDER BY id',('/incoming_image',)):
                details=json.loads(row['metadata'] or '{}')
                if details.get('media_group_id')==metadata['media_group_id']:group.append({**details,'update_id':row['id']})
            if group and time.time()-max(m.get('received_at',0) for m in group)<2.5:return False
            if group:
                leader=min(m['update_id'] for m in group)
                if update['id']!=leader:
                    with self.l.db:self.l.db.execute("UPDATE local_updates SET state='grouped' WHERE id=?",(update['id'],))
                    return True
                metadata['album_members']=group
                with self.l.db:
                    for member in group:
                        if member['update_id']!=leader:self.l.db.execute("UPDATE local_updates SET state='grouped' WHERE id=?",(member['update_id'],))
        if update['state']=='pending':
            with self.l.db: self.l.db.execute("UPDATE local_updates SET state='processing' WHERE id=?",(update['id'],))
            incoming_voice = update['text']=='/incoming_voice'
            mode_row = self.l.rows("SELECT value FROM local_meta WHERE key='voice_mode'")
            mode = mode_row[0]['value'] if mode_row else os.environ.get('FINANCE_VOICE_MODE','off')
            wants_voice = mode=='on' or (mode=='auto' and incoming_voice)
            text = update['text']
            control = False
            try:
                if incoming_voice:
                    path = ROOT/'data/voice-inbox'/f"{update['id']}.audio"
                    if not path.exists(): self.telegram.download_voice(metadata,path)
                    transcript = metadata.get('transcript') or self.voice.transcribe(path)
                    metadata['transcript'] = transcript
                    # Persist the transcript before the trusted received-income
                    # controller binds it to this authenticated voice envelope.
                    with self.l.db:self.l.db.execute('UPDATE local_updates SET metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),update['id']))
                    # STT is untrusted input and cannot execute /confirm, /voice or any slash command.
                    text = 'Непроверенная транскрипция речи пользователя (суммы и даты подтвердить отдельно):\n'+transcript
                if text in ('/incoming_pdf','/incoming_image','/incoming_text','/incoming_unsupported'):
                    answer=self.attachments.handle(update,metadata)
                elif text.startswith('/retryvoice '):
                    original_id = int(text.split(maxsplit=1)[1])
                    original = self.l.rows('SELECT * FROM local_updates WHERE id=? AND chat=?',(original_id,OWNER))
                    prior = json.loads(original[0]['metadata'] or '{}') if original else {}
                    if not original or not prior.get('voice_failed'):
                        answer='Не найдена неудачная голосовая запись для повтора.'
                    else:
                        prior.pop('voice_failed',None)
                        with self.l.db: self.l.db.execute("UPDATE local_updates SET state='pending',metadata=? WHERE id=?",(json.dumps(prior,ensure_ascii=False),original_id))
                        answer='Повторная обработка сохранённой записи поставлена в очередь.'
                    wants_voice=False
                elif text=='/settings':
                    answer='Формат ответа: '+{'on':'голосом','off':'текстом','auto':'голосом на голосовые'}.get(mode,'текстом')+'. Выберите «✍️ Текстом» или «🎙 Голосом» на клавиатуре под полем ввода. Настройка сохраняется до следующего переключения.'
                    if hasattr(self.assistant,'reply_settings'):answer+='\n\n'+self.assistant.reply_settings()
                    control=True;wants_voice=False
                elif text=='/engine' or text.startswith('/engine '):
                    answer=self.assistant.handle(OWNER,text,f"telegram:{OWNER}:{update['id']}:0")
                    control=True;wants_voice=False
                elif text=='/voice' or text.startswith('/voice '):
                    selected = text.split(maxsplit=1)[1].strip().lower() if ' ' in text else mode
                    selected = {'авто':'auto','голос':'on','текст':'off'}.get(selected,selected)
                    if selected not in ('auto','on','off'):
                        answer = 'Режимы: /voice auto — голос на голос; /voice on — озвучивать ответы; /voice off — только текст.'
                    else:
                        with self.l.db: self.l.db.execute("INSERT OR REPLACE INTO local_meta VALUES('voice_mode',?)",(selected,))
                        answer = {'auto':'На голосовые отвечаю голосом, на текст — текстом.' if self.voice_only else 'На голосовые отвечаю текстом и голосом, на текст — текстом.','on':'Буду отвечать голосом.' if self.voice_only else 'Буду отвечать текстом и голосом.','off':'Буду отвечать только текстом. Голосовой ввод остаётся доступен.'}[selected]
                        if selected=='on' and not self.voice:answer+=' Сейчас голосовой движок недоступен: пока отправлю текст, выбранный режим сохранён.'
                    control=True;wants_voice=False
                elif text=='/unsupported_media':
                    answer='Поддерживаются текст, голос, PDF, TXT и фотографии JPEG/PNG, в том числе альбомы.'
                    wants_voice=False
                else:
                    if text.startswith('/say '): text=text[5:].strip();wants_voice=True
                    answer = self.assistant.handle(OWNER,text,f"telegram:{OWNER}:{update['id']}:0")
                    # Iva interprets arbitrary owner wording and calls reply_format.
                    # Read the decision before delivery (also for a spoken stop).
                    latest=json.loads(self.l.rows('SELECT metadata FROM local_updates WHERE id=?',(update['id'],))[0]['metadata'] or '{}')
                    metadata.update(latest)
                    if 'reply_voice_requested' in latest:wants_voice=latest['reply_voice_requested']
                spoken_answer = answer
                if incoming_voice: answer = 'Распознано (проверьте суммы и даты):\n'+metadata['transcript'][:900]+'\n\n'+answer
                if wants_voice:
                    if not self.voice:
                        answer+='\nГолосовой движок не готов; отправлен текст.'
                    else:
                        try:
                            content=self.voice.synthesize(spoken_answer)
                            target=ROOT/'data/voice-outbox'/f"{update['id']}.ogg"
                            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
                            metadata['reply_voice']=True
                            metadata['reply_text']=not self.voice_only
                        except VoiceError as exc: answer+='\n'+str(exc)
            except (VoiceError,ValueError,KeyError,TypeError,RuntimeError) as exc:
                attachment=text in ('/incoming_pdf','/incoming_image','/incoming_text')
                answer = str(exc) if isinstance(exc,(VoiceError,RuntimeError,ValueError)) else ('Не удалось однозначно разобрать поля вложения. Даты и суммы не выдумываются.' if attachment else 'Не удалось обработать запрос; финансовые изменения не выполнены.')
                metadata['error_type']=type(exc).__name__
                if incoming_voice and not metadata.get('transcript'):
                    metadata['voice_failed']=True
                    answer+=f"\nЗапись сохранена. Повторить: /retryvoice {update['id']}"
                if incoming_voice and metadata.get('transcript'):
                    metadata['processing_failed']=True
                    answer+='\nГолос распознан и сохранён; сбой произошёл при выполнении запроса.'
                if text in ('/incoming_pdf','/incoming_image','/incoming_text'):answer+='\nОригинал/метаданные сохранены. Результаты действий требуют проверки.'
                wants_voice=False
            except Exception as exc:
                metadata['error_type']=type(exc).__name__
                answer='Не удалось прочитать вложение или завершить извлечение. Оригинал и метаданные, если получены, сохранены. Банковские реквизиты не выводятся. Для защищённого PDF нужна разблокированная копия; для повреждённого файла — исправный оригинал.'
                wants_voice=False
            with self.l.db: self.l.db.execute("UPDATE local_updates SET state='ready',answer=?,metadata=? WHERE id=?",(answer,json.dumps(metadata,ensure_ascii=False),update['id']))
        else: answer = update['answer']
        # Claim before sending. Never automatically retry an uncertain delivery.
        with self.l.db: self.l.db.execute("UPDATE local_updates SET state='sending' WHERE id=?",(update['id'],))
        try:
            if metadata.get('reply_text',True) or not metadata.get('reply_voice'):self.telegram.send(OWNER,answer)
            if metadata.get('reply_voice'):
                self.telegram.send_voice(OWNER,(ROOT/'data/voice-outbox'/f"{update['id']}.ogg").read_bytes())
            state = 'done'
        except RuntimeError:
            state = 'uncertain'
            print('Ответ Telegram не подтверждён. Проверьте чат; автоматического повтора нет.',flush=True)
        with self.l.db: self.l.db.execute('UPDATE local_updates SET state=? WHERE id=?',(state,update['id']))
        if metadata.get('album_members'):
            with self.l.db:
                for member in metadata['album_members']:
                    if member['update_id']!=update['id']:self.l.db.execute('UPDATE local_updates SET state=? WHERE id=?',('done_group' if state=='done' else 'uncertain',member['update_id']))
        if state=='done' and not metadata.get('voice_failed'):
            (ROOT/'data/voice-inbox'/f"{update['id']}.audio").unlink(missing_ok=True)
            (ROOT/'data/voice-outbox'/f"{update['id']}.ogg").unlink(missing_ok=True)
        return True

    def recover(self):
        with self.l.db:
            # No blind retry of confirmations after crash: balances may already have changed.
            self.l.db.execute("UPDATE local_updates SET state='uncertain' WHERE state IN ('processing','sending')")
            self.l.db.execute("UPDATE local_pending SET state='uncertain' WHERE state='processing'")

    def run(self):
        self.recover()
        me = self.telegram.call('getMe',{})
        webhook = self.telegram.call('getWebhookInfo',{})
        if webhook.get('url'): raise RuntimeError('У бота настроен webhook. Отключите прежний транспорт перед локальным polling')
        print('Локальный бот запущен. Один владелец. Голос: '+('готов' if self.voice else 'ожидает локальные зависимости; входящие сохраняются в очереди')+'.',flush=True)
        while True:
            if self.process_one(): continue
            self.check_reminders()
            offset = self.l.rows("SELECT value FROM local_meta WHERE key='offset'")
            try:
                updates = self.telegram.call('getUpdates',{'offset':int(offset[0]['value']) if offset else 0,'timeout':25,'allowed_updates':['message']})
                self.ingest(updates)
            except TelegramFatal: raise
            except RuntimeError:
                print('Telegram временно недоступен; следующая попытка через 5 секунд.',flush=True)
                time.sleep(5)


class ProcessLock:
    def __init__(self,path): self.path=path
    def __enter__(self):
        self.file = open(self.path,'a+b')
        self.file.seek(0); self.file.write(b'0'); self.file.flush(); self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close(); raise RuntimeError('Этот локальный бот уже запущен') from None
        return self
    def __exit__(self,*args): self.file.close()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    runtime=ROOT/'data/iva-runtime.json'
    if runtime.exists() and json.loads(runtime.read_text(encoding='utf-8')).get('mode')=='telegram':
        raise ValueError('Telegram подключён к Ива. Старый финансовый бот отключён; используйте scripts/Start-Iva.ps1.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--env',default='.env')
    parser.add_argument('--db',default='data/local-finance.sqlite3')
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args()
    load_env(Path(args.env))
    if os.environ.get('FINANCE_ALLOWED_USER_ID',str(OWNER))!=str(OWNER):
        raise ValueError('Разрешён только согласованный Telegram ID')
    if not 0<=int(os.environ.get('FINANCE_DEFAULT_LEAD_DAYS','1'))<=365:
        raise ValueError('FINANCE_DEFAULT_LEAD_DAYS: 0..365')
    cli = Codex(timeout=int(os.environ.get('FINANCE_CODEX_TIMEOUT','150')))
    if args.check:
        print('CLI:',cli.plan('Проверка: ответь кратко по-русски, action=none payload_json={}. Не используй инструменты.')[0])
        print('Telegram token configured:',bool(os.environ.get('TELEGRAM_BOT_TOKEN')))
        print('Allowed user:',OWNER)
        return
    token = os.environ.get('TELEGRAM_BOT_TOKEN','')
    if not token: raise ValueError('Токен не настроен. Запустите scripts/Configure-Local.ps1')
    Path(args.db).parent.mkdir(parents=True,exist_ok=True)
    with ProcessLock(str(args.db)+'.lock'):
        ledger = Ledger(args.db)
        Bot(ledger,Assistant(ledger,cli),Telegram(token)).run()


if __name__=='__main__':
    try: main()
    except CliError as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
    except (ValueError,RuntimeError,sqlite3.Error) as exc:
        # All application ValueError/RuntimeError messages here are authored locally,
        # Telegram.call strips remote errors and token-bearing URLs first.
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
