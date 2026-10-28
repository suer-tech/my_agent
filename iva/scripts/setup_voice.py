"""Pinned local voice runtime. Downloads public binaries/models, never credentials."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'/'voice'
RELEASE = 'v1.9.2'  # Latest stable tag with the Windows CPU release asset checked at implementation.
ZIP_SHA = '49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a'


def download(url,path,expected,max_bytes):
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==expected: return
    part = path.with_suffix(path.suffix+'.part')
    digest = hashlib.sha256(); size=0
    try:
        with urllib.request.urlopen(url,timeout=90) as response, part.open('wb') as output:
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>max_bytes: raise ValueError('Download exceeds expected size limit')
                digest.update(block);output.write(block)
        if digest.hexdigest()!=expected: raise ValueError('SHA256 verification failed')
        part.replace(path)
    finally: part.unlink(missing_ok=True)


def main():
    DATA.mkdir(parents=True,exist_ok=True)
    runtime = ROOT/'data'/'voice-runtime'
    if not (runtime/'imageio_ffmpeg').exists():
        subprocess.run([sys.executable,'-m','pip','install','--target',str(runtime),'imageio-ffmpeg==0.6.0'],check=True)
    archive = DATA/'whisper-bin-x64.zip'
    print('Downloading and verifying whisper.cpp CPU release...',flush=True)
    download(f'https://github.com/ggml-org/whisper.cpp/releases/download/{RELEASE}/whisper-bin-x64.zip',archive,ZIP_SHA,20*1024*1024)
    destination = (DATA/'whisper').resolve()
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            target = (destination/item.filename).resolve()
            if not target.is_relative_to(destination): raise ValueError('Unsafe zip member')
            if item.file_size>100*1024*1024: raise ValueError('Unexpected archive size')
        zipped.extractall(destination)
    print('Fetching published model SHA256...',flush=True)
    metadata = json.load(urllib.request.urlopen('https://huggingface.co/api/models/ggerganov/whisper.cpp?blobs=true',timeout=45))
    model = next(s for s in metadata['siblings'] if s['rfilename']=='ggml-base.bin')
    sha = model['lfs']['sha256']
    print('Downloading multilingual base model (about 142 MiB)...',flush=True)
    download('https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin',DATA/'ggml-base.bin',sha,160*1024*1024)
    manifest = {'whisper_release':RELEASE,'archive_sha256':ZIP_SHA,'model':'ggml-base.bin','model_sha256':sha,'imageio_ffmpeg':'0.6.0'}
    (DATA/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
