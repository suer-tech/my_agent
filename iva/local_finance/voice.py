"""Local STT; configurable Cartesia or Windows TTS, with local Opus conversion."""
from array import array
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 12*1024*1024
MAX_SECONDS = 180


class VoiceError(RuntimeError): pass


def spoken_chunks(text,limit=3500):
    text=re.sub(r'/confirm\s+[a-f0-9]+','Подтверждение выполните командой в текстовом сообщении.',text)
    text=re.sub(r'/cancel\s+[a-f0-9]+','Отмена доступна в текстовом сообщении.',text).replace('RUB','рублей').strip()
    chunks=[]
    while text:
        end=min(len(text),limit)
        if len(text)>limit:
            boundaries=list(re.finditer(r'(?:[.!?]\s+|\n+|\s+)',text[:limit+1]))
            candidates=[m.end() for m in boundaries if limit//2<=m.end()<=limit]
            if candidates:end=candidates[-1]
        chunks.append(text[:end].strip());text=text[end:].lstrip()
    return chunks


class Voice:
    def __init__(self):
        self.ffmpeg = next((ROOT/'data/voice-runtime/imageio_ffmpeg/binaries').glob('*.exe'),None)
        self.whisper = next((ROOT/'data/voice/whisper').rglob('whisper-cli.exe'),None)
        if os.name!='nt':
            self.ffmpeg=Path(shutil.which('ffmpeg')) if shutil.which('ffmpeg') else None
            candidate=ROOT/'data/voice/whisper/build/bin/whisper-cli'
            installed=shutil.which('whisper-cli')
            self.whisper=candidate if candidate.is_file() else Path(installed) if installed else None
        self.model = ROOT/'data/voice/ggml-base.bin'

    @property
    def ready(self): return bool(self.ffmpeg and self.whisper and self.model.is_file())

    def run(self,args,stdin=None,timeout=90):
        try:
            result = subprocess.run([str(a) for a in args],input=stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                    timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                                    env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('TOKEN','API_KEY','SECRET','PASSWORD'))})
        except subprocess.TimeoutExpired: raise VoiceError('Обработка голоса превысила таймаут. Запись сохранена; можно повторить позже.') from None
        except OSError: raise VoiceError('Локальный голосовой движок не запускается; проверьте setup_voice.py') from None
        if result.returncode: raise VoiceError('Не удалось обработать аудио локально. Пришлите запись ещё раз или текст.')
        return result

    def transcribe(self,source):
        if not self.ready: raise VoiceError('Локальное распознавание ещё не установлено; голосовая запись сохранена в очереди.')
        if Path(source).stat().st_size>MAX_BYTES: raise VoiceError('Запись слишком большая: максимум 12 MiB и 3 минуты.')
        with tempfile.TemporaryDirectory(prefix='finance-stt-') as temp:
            wav,prefix = Path(temp)/'input.wav',Path(temp)/'transcript'
            self.run([self.ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y','-protocol_whitelist','file,pipe','-i',source,'-t','181','-ar','16000','-ac','1','-c:a','pcm_s16le',wav])
            with wave.open(str(wav)) as audio:
                if audio.getnframes()/audio.getframerate()>MAX_SECONDS: raise VoiceError('Запись длиннее 3 минут. Разбейте её на короткие сообщения.')
                samples = array('h',audio.readframes(audio.getnframes()))
                if not samples or max(abs(v) for v in samples)<100: raise VoiceError('В записи не слышно речи. Пришлите более громкую запись или текст.')
            self.run([self.whisper,'-m',self.model,'-f',wav,'-l','ru','-otxt','-of',prefix,'-nt','-np','-t',str(min(os.cpu_count() or 2,8))],timeout=180)
            path = Path(str(prefix)+'.txt')
            if not path.is_file(): raise VoiceError('Распознавание не вернуло текст; финансовых изменений нет.')
            text = path.read_text(encoding='utf-8-sig').strip()
            if not text or text in ('[BLANK_AUDIO]','[Silence]') or len(text)>12000:
                raise VoiceError('Речь не распознана уверенно. Пришлите короткую запись или текст.')
            return text

    def synthesize_wav(self,spoken,wav):
        from .cartesia import Cartesia,CartesiaError,settings
        if settings().get('enabled'):
            try:wav.write_bytes(Cartesia().synthesize_wav(spoken))
            except CartesiaError as error:raise VoiceError(str(error)) from None
        else:
            self.run(['powershell.exe','-NoProfile','-File',ROOT/'scripts/Speak-Local.ps1','-Output',wav],stdin=spoken.encode('utf-8'))

    def synthesize(self,text):
        if not self.ffmpeg: raise VoiceError('Не установлен конвертер голосового ответа; текстовый ответ сохранён.')
        chunks=spoken_chunks(text)
        if not chunks: raise VoiceError('Нет текста для озвучивания')
        with tempfile.TemporaryDirectory(prefix='finance-tts-') as temp:
            ogg=Path(temp)/'reply.ogg';inputs=[]
            for i,spoken in enumerate(chunks):
                wav=Path(temp)/f'part-{i}.wav';self.synthesize_wav(spoken,wav);inputs+=['-i',wav]
            concat=[] if len(chunks)==1 else ['-filter_complex',''.join(f'[{i}:a]' for i in range(len(chunks)))+f'concat=n={len(chunks)}:v=0:a=1[out]','-map','[out]']
            self.run([self.ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y',*inputs,*concat,'-c:a','libopus','-b:a','32k','-ac','1','-ar','48000','-application','voip',ogg])
            content = ogg.read_bytes()
            if not content.startswith(b'OggS') or b'OpusHead' not in content[:512] or len(content)>MAX_BYTES:
                raise VoiceError('Конвертер не создал корректный OGG/Opus')
            return content
