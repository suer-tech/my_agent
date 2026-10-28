"""Offline Russian Windows TTS test. Does not send anything to Telegram."""
from pathlib import Path
import subprocess
import wave

root = Path(__file__).resolve().parents[1]
output = root/'data'/'windows-tts-test.wav'
output.parent.mkdir(parents=True,exist_ok=True)
subprocess.run(['powershell.exe','-NoProfile','-File',str(root/'scripts'/'Speak-Local.ps1'),'-Output',str(output)],
               input='Проверка русского голоса. Минимальный остаток двадцать тысяч рублей.'.encode('utf-8'),
               check=True,creationflags=subprocess.CREATE_NO_WINDOW)
with wave.open(str(output)) as audio:
    print({'output':str(output),'sample_rate':audio.getframerate(),'seconds':round(audio.getnframes()/audio.getframerate(),2)})
