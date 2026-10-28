"""Read-only cutover fingerprints. Outputs hashes/counts, never financial rows or secrets."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT=Path('/opt/iva')
DATABASES=['data/local-finance.sqlite3','data/personal-skills/state.sqlite3','data/tender-agent.sqlite3','data/disk-storage/audit.sqlite3']


def audit():
    result={'databases':{},'files':{}}
    for relative in DATABASES:
        p=ROOT/relative
        if not p.exists():continue
        tables={}
        with closing(sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True)) as c:
            integrity=c.execute('PRAGMA quick_check').fetchone()[0]
            for name, in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                rows=c.execute('SELECT * FROM "'+name.replace('"','""')+'"').fetchall()
                encoded=sorted(json.dumps(row,ensure_ascii=False,default=lambda x:{'bytes':bytes(x).hex()},separators=(',',':')) for row in rows)
                tables[name]={'count':len(rows),'sha256':hashlib.sha256(('\n'.join(encoded)).encode()).hexdigest()}
        result['databases'][relative]={'integrity':integrity,'tables':tables}
    for relative in ['data/documents','data/iva-pilot/app/vault','data/iva-pilot/app/data/custom/agent/skills']:
        folder=ROOT/relative
        for p in sorted(folder.rglob('*')) if folder.exists() else []:
            if not p.is_file() or p.is_symlink() or '.git' in p.parts:continue
            result['files'][str(p.relative_to(ROOT))]=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
    return result


if __name__=='__main__':print(json.dumps(audit(),ensure_ascii=False,sort_keys=True))
