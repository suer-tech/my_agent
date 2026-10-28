"""Bounded Cartesia TTS; credentials stay outside prompts and source code."""
import hashlib
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
import wave

ROOT=Path(__file__).resolve().parents[1]
REQUEST_LOCK=threading.Lock()


class CartesiaError(RuntimeError):pass


def settings(root=ROOT):
    path=Path(root)/'data/cartesia-settings.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'enabled':False}


class Cartesia:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.config=settings(self.root)

    def synthesize_wav(self,text):
        if not self.config.get('enabled'):raise CartesiaError('Cartesia TTS не включён')
        if not isinstance(text,str) or not text.strip() or len(text)>3500:raise CartesiaError('Для озвучивания нужен текст до 3500 символов')
        key_file=self.root/'data/cartesia.env'
        try:
            values=dict(line.split('=',1) for line in key_file.read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.lstrip().startswith('#'))
            key=values['CARTESIA_API_KEY'].strip()
            if not key:raise ValueError()
        except (OSError,KeyError,ValueError):raise CartesiaError('Ключ Cartesia недоступен в локальной конфигурации') from None
        model=self.config['model_id'];voice=self.config['voice_id']
        payload={'model_id':model,'transcript':text.strip(),'voice':voice,'language':'ru','output_format':{'container':'wav','encoding':'pcm_s16le','sample_rate':44100},'generation_config':{'speed':1,'volume':1}}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        cache=self.root/'data/tts-cache/cartesia';cache.mkdir(parents=True,exist_ok=True);target=cache/(digest+'.wav')
        with REQUEST_LOCK:
            if target.is_file():
                audio=target.read_bytes();self.validate(audio);return audio
            request=urllib.request.Request('https://api.cartesia.ai/tts/bytes',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Cartesia-Version':self.config.get('api_version','2026-08-14'),'Content-Type':'application/json'})
            try:
                # No implicit retries: uncertain synthesis must not duplicate charges.
                with urllib.request.urlopen(request,timeout=35) as response:audio=response.read(16*1024*1024+1)
            except urllib.error.HTTPError as error:
                status=error.code;error.close()
                reason={401:'Cartesia отклонила ключ',403:'Cartesia не разрешила синтез',402:'Недостаточно кредитов Cartesia',429:'Лимит запросов Cartesia'}.get(status,'Ошибка Cartesia HTTP '+str(status))
                raise CartesiaError(reason+'; текстовый ответ сохранён') from None
            except (OSError,TimeoutError):raise CartesiaError('Cartesia недоступна или превысила таймаут; текстовый ответ сохранён') from None
            self.validate(audio)
            temp=target.with_suffix('.tmp');temp.write_bytes(audio);temp.replace(target)
            return audio

    @staticmethod
    def validate(audio):
        if not audio.startswith(b'RIFF') or len(audio)>16*1024*1024:raise CartesiaError('Cartesia вернула некорректное аудио')
        try:
            with wave.open(io.BytesIO(audio)) as source:
                # Streaming WAV may use an unknown-length RIFF/data sentinel.
                # Validate the actual received samples rather than that header.
                frames=source.readframes(16*1024*1024)
                seconds=len(frames)/(source.getframerate()*source.getnchannels()*source.getsampwidth())
                if source.getnchannels()!=1 or source.getsampwidth()!=2 or not .1<seconds<=300:raise ValueError()
        except (wave.Error,EOFError,ValueError,ZeroDivisionError):raise CartesiaError('Cartesia вернула некорректный WAV') from None
