"""Optional owner-configured TTS adapter. No model-callable shell tool."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from finance.delivery import telegram


def synthesize(text, command, output):
    if not text.strip() or len(text)>4000: raise ValueError('Текст: 1..4000 символов')
    if not isinstance(command,list) or not command or not all(isinstance(x,str) for x in command):
        raise ValueError('FINANCE_TTS_COMMAND_JSON: JSON-массив аргументов')
    if not any('{output}' in arg for arg in command): raise ValueError('Нужен {output} для OGG/Opus')
    argv = [arg.replace('{output}',str(output)) for arg in command]
    # An executable configured by VPS owner only. Text is stdin, never shell syntax.
    subprocess.run(argv,input=text.encode('utf-8'),check=True,timeout=90,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    content = Path(output).read_bytes()
    if not content.startswith(b'OggS') or b'OpusHead' not in content[:512] or len(content)>20*1024*1024:
        raise ValueError('Адаптер должен создать OGG/Opus до 20 МБ')
    return content


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',help='Путь .ogg для локальной проверки')
    parser.add_argument('--send',action='store_true')
    args = parser.parse_args()
    text = sys.stdin.read()
    command = json.loads(os.environ.get('FINANCE_TTS_COMMAND_JSON','[]'))
    with tempfile.TemporaryDirectory() as temp:
        content = synthesize(text,command,Path(temp)/'reply.ogg')
        if args.output: Path(args.output).write_bytes(content)
        if args.send: telegram('sendVoice',{},content)
    print('Голосовой ответ обработан')
