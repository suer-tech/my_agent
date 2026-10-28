"""Create a PRIVATE, portable cutover snapshot; never stop services implicitly."""
import argparse
import io
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
REMOTE='/opt/iva'
SKIP={'node_modules','__pycache__','.git','.output','builds','sandbox-cache'}


def relocate(value,old_root,new_root=REMOTE):
    if isinstance(value,dict):return {k:relocate(v,old_root,new_root) for k,v in value.items()}
    if isinstance(value,list):return [relocate(v,old_root,new_root) for v in value]
    if isinstance(value,str):
        normalized=value.replace('\\','/')
        old=str(old_root).replace('\\','/').rstrip('/')
        if normalized.casefold()==old.casefold() or normalized.casefold().startswith(old.casefold()+'/'):
            return new_root+normalized[len(old):]
    return value


def database_snapshot(target):
    source=sqlite3.connect(ROOT/'data/local-finance.sqlite3')
    dest=sqlite3.connect(target)
    try:
        source.backup(dest)
        # Only operational paths/JSON, not identity keys or historical chat text.
        for table in ['source_documents','local_updates','local_meta']:
            columns=[r[1] for r in dest.execute(f'PRAGMA table_info("{table}")') if r[1] in {'path','metadata','extraction','value'}]
            for column in columns:
                for rowid,text in dest.execute(f'SELECT rowid,"{column}" FROM "{table}"').fetchall():
                    if not isinstance(text,str):continue
                    try:updated=json.dumps(relocate(json.loads(text),ROOT),ensure_ascii=False)
                    except ValueError:updated=relocate(text,ROOT)
                    if updated!=text:dest.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',(updated,rowid))
        dest.commit()
        if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Snapshot integrity failed')
    finally:source.close();dest.close()


def files():
    roots=['local_finance','iva_integration','plugin','scripts','deploy','docs','config','tests',
           'data/iva-pilot/app/agent','data/iva-pilot/app/assets','data/iva-pilot/app/bin',
           'data/iva-pilot/app/packages','data/iva-pilot/app/scripts','data/iva-pilot/app/services',
           'data/iva-pilot/app/skills','data/iva-pilot/app/vault','data/iva-pilot/app/vault-template',
           'data/iva-pilot/app/data','data/documents','data/iva-telegram/turns']
    for root in roots:
        for path in (ROOT/root).rglob('*'):
            skipped=SKIP- {'.git'} if root=='data/iva-pilot/app/vault' else SKIP
            if path.is_file() and not any(part in skipped for part in path.relative_to(ROOT/root).parts) and not path.is_symlink():yield path
    for name in ['.env','README.md','requirements-browser.txt','data/cartesia.env','data/cartesia-settings.json',
                 'data/iva-agents.json','data/iva-model.json','data/iva-runtime.json','data/iva-pilot/control.json',
                 'data/iva-pilot/memory-scheduler.json',
                 'data/iva-pilot/app/.env','data/iva-pilot/app/.npmrc','data/iva-pilot/app/package.json',
                 'data/iva-pilot/app/package-lock.json','data/iva-pilot/app/eve.lock.json','data/iva-pilot/app/tsconfig.json',
                 'data/voice/ggml-base.bin']:
        path=ROOT/name
        if path.is_file():yield path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cutover',action='store_true');parser.add_argument('--without-model',action='store_true');args=parser.parse_args()
    selected=sorted(set(files()))
    if args.without_model:selected=[p for p in selected if p!=ROOT/'data/voice/ggml-base.bin']
    if not args.cutover:
        print(json.dumps({'ready_to_package':True,'files':len(selected),'bytes':sum(p.stat().st_size for p in selected),'cutover_requires_stopped_local_services':True}));return
    state=json.loads((ROOT/'data/iva-telegram/session.json').read_text(encoding='utf-8'))
    status=json.loads((ROOT/'data/iva-telegram/status.json').read_text(encoding='utf-8'))
    if state.get('active_source') or status.get('state')!='stopped' or not (ROOT/'data/iva-telegram/stop').exists():raise RuntimeError('Wait for Telegram to stop and finish its active turn before the final snapshot')
    memory=ROOT/'data/iva-pilot/memory-scheduler.json'
    if memory.exists() and json.loads(memory.read_text()).get('running'):raise RuntimeError('Wait for active memory maintenance')
    if not (ROOT/'data/iva-pilot/memory-scheduler.stop').exists():raise RuntimeError('Stop local memory scheduler before the final snapshot')
    auth=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'auth.json'
    if not auth.is_file():raise RuntimeError('Codex auth cache is missing')
    output=ROOT/'data/vps-deploy';output.mkdir(exist_ok=True)
    package=output/('iva-'+str(time.time_ns())+'.tar.gz')
    with tempfile.TemporaryDirectory() as temp,tarfile.open(package,'w:gz') as archive:
        def put(name,content):
            info=tarfile.TarInfo(name);info.size=len(content);info.mode=0o600;archive.addfile(info,io.BytesIO(content))
        for path in selected:
            rel=path.relative_to(ROOT).as_posix();content=path.read_bytes()
            if rel in ('data/iva-runtime.json','data/iva-pilot/memory-scheduler.json') or rel.startswith('data/iva-pilot/app/data/') and rel.endswith('.json') or rel.startswith('data/documents/') and path.name in ('original-album.json','extraction-album.json'):
                content=json.dumps(relocate(json.loads(content),ROOT),ensure_ascii=False).encode()
            elif rel in ('data/iva-pilot/app/.env','.env'):
                lines=[]
                for line in content.decode('utf-8').splitlines():
                    if '=' in line:
                        key,value=line.split('=',1);line=key+'='+relocate(value,ROOT)
                    lines.append(line)
                network=ROOT/'data/vps-deploy/network.json'
                if network.exists():
                    extras=json.loads(network.read_text())['app_env' if rel!='.env' else 'root_env']
                    lines=[line for line in lines if line.split('=',1)[0] not in extras]
                    lines.extend(key+'='+value for key,value in extras.items())
                content=('\n'.join(lines)+'\n').encode()
            put('opt/iva/'+rel,content)
        db=Path(temp)/'finance.sqlite3';database_snapshot(db)
        put('opt/iva/data/local-finance.sqlite3',db.read_bytes())
        conn=sqlite3.connect(db);conn.row_factory=sqlite3.Row
        operations=[dict(r) for r in conn.execute('SELECT * FROM operations ORDER BY id')]
        manifest={'operations_count':len(operations),'operations_sha256':hashlib.sha256(json.dumps(operations,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'voice_model_sha256':hashlib.sha256((ROOT/'data/voice/ggml-base.bin').read_bytes()).hexdigest(),
                  'table_counts':{table:conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['accounts','family_expenses','source_documents','local_updates','local_pending']}}
        conn.close()
        put('opt/iva/data/migration-manifest.json',json.dumps(manifest).encode())
        put('opt/iva/data/iva-telegram/session-before-vps.json',(ROOT/'data/iva-telegram/session.json').read_bytes())
        put('opt/iva/data/iva-telegram/session.json',b'{}')
        put('home/iva/.codex/auth.json',auth.read_bytes())
    os.chmod(package,0o600)
    print(json.dumps({'private_package':str(package),'bytes':package.stat().st_size,'contains_credentials':True}))


if __name__=='__main__':main()
