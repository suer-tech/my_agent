"""One-time repair of the already-authorized three-hour mail task, no finance writes."""
import json
import sqlite3
import sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'/opt/iva')
from iva_integration.personal_mail import execute

path=Path('/opt/iva/data/repair-20261024/mail-job-before.json')
db=sqlite3.connect('/opt/iva/data/local-finance.sqlite3');db.row_factory=sqlite3.Row
try:
    row=db.execute("SELECT * FROM scheduled_jobs WHERE id='42673be348a54cd4'").fetchone()
    spec=json.loads(row['spec'])
    if spec['kind']=='mail_watch':print('Already migrated; cursor preserved');sys.exit(0)
    if row['state']!='active' or spec['interval_minutes']!=180 or spec['folder']!='INBOX':raise RuntimeError('Task changed; do not overwrite')
    live=execute({'action':'list','limit':1})
    if not live.get('ok') or live.get('uidvalidity')!='1428422225':raise RuntimeError('Mailbox must be verified before migration')
    path.write_text(json.dumps(dict(row),ensure_ascii=False));path.chmod(0o600)
    # UID 3093/3094 were summarized in Telegram 2580/2572; 3095/3096 in 2572/2593.
    # Start before October 23 mail, preserving unreported auction/webinar/new arrivals.
    cursor={'uidvalidity':live['uidvalidity'],'last_uid':3089,'reported_uids':[3093,3094,3095,3096],'backfill_through':3097}
    spec.update(kind='mail_watch',queries=[])
    with db:
        count=db.execute("UPDATE scheduled_jobs SET spec=?,cursor=?,next_at=?,error=NULL,failures=0 WHERE id=? AND state='active' AND spec=?",(json.dumps(spec,ensure_ascii=False),json.dumps(cursor),datetime.now(timezone.utc).isoformat(timespec='seconds'),row['id'],row['spec'])).rowcount
        if count!=1:raise RuntimeError('Task changed during migration')
    print(json.dumps({'migrated':True,'last_uid':3089,'excluded_previously_reported':[3093,3094,3095,3096]}))
finally:db.close()
