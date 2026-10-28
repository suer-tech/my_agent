"""Save bounded live evidence without email bodies, prompts or credentials."""
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
sys.path.insert(0,'/opt/iva')
from iva_integration.mail_digest import poll
from iva_integration.threads_alerts import coordinator_status

root=Path('/opt/iva/data/repair-20261024')
with closing(sqlite3.connect('file:/opt/iva/data/local-finance.sqlite3?mode=ro',uri=True)) as db:
    db.row_factory=sqlite3.Row
    row=db.execute("SELECT spec,cursor,state,next_at,failures FROM scheduled_jobs WHERE id='42673be348a54cd4'").fetchone()
    cursor=json.loads(row['cursor']);result=poll(json.loads(row['spec']),cursor)
    last_run=dict(db.execute("SELECT id,state,started,finished FROM scheduled_runs WHERE job_id='42673be348a54cd4' ORDER BY started DESC LIMIT 1").fetchone())
    delivery=[dict(r) for r in db.execute('SELECT state,count(*) count FROM scheduled_outbox WHERE run_id=? GROUP BY state',(last_run['id'],))]
    mail={'job_state':row['state'],'next_at':row['next_at'],'failures':row['failures'],'cursor':cursor,
          'last_run':last_run,'delivery':delivery,'repeat_probe':{k:result.get(k) for k in ('ok','notify','processed_uids','error')}}

s=coordinator_status();browser=Path('/opt/iva/data/threads-browser')
evidence={'checked_at':time.time(),'mail':mail,'core':s.get('iva_runtime_health'),
          'replies':json.loads((root/'replies/verification.json').read_text()),
          'primary':json.loads((root/('primary-v2' if (root/'primary-v2/verification.json').exists() else 'primary')/'verification.json').read_text()),
          'threads':{'extension':s.get('threads_promotion'),'development':s.get('threads_development',{}).get('progress'),
                     'inbox':s.get('threads_messages'),'alerts':s.get('threads_alerts'),
                     'editor':json.loads((browser/'editor-preflight-status.json').read_text()) if (browser/'editor-preflight-status.json').exists() else None,
                     'new_publication_confirmed':False}}
for campaign,path in [('extension',browser),('development',browser/'development')]:
    with closing(sqlite3.connect((path/'hourly.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT count(*) FROM posts WHERE state='sent' AND reply_url IS NOT NULL").fetchone()[0]:evidence['threads']['new_publication_confirmed']=True
p=root/'live-verification.json';p.write_text(json.dumps(evidence,ensure_ascii=False,indent=2));p.chmod(0o600)
print(json.dumps({'mail_repeat':mail['repeat_probe'],'mail_delivery':delivery,'live_reply_checks':sum(r['ok'] for r in evidence['replies']),
                  'editor_verified':bool(evidence['threads']['editor']),'new_publication_confirmed':evidence['threads']['new_publication_confirmed']},ensure_ascii=False))
