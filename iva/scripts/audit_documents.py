"""Document-processing health only; excludes personal values, text and filenames."""
import json
from pathlib import Path
import re
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
db=sqlite3.connect(ROOT/'data/local-finance.sqlite3');db.row_factory=sqlite3.Row
docs=[]
for row in db.execute('SELECT * FROM source_documents'):
    data=json.loads(row['extraction']);rows=data.get('rows',[]);questions=data.get('questions',[])
    docs.append({'kind':row['kind'],'status':row['status'],'current_review_source':row['status']!='album_member','pages':row['pages'],'rows':len(rows),'parser':data.get('parser','codex-vision'),
                 'vision_inputs':data.get('vision_input_count'),'missing_payment_amounts':sum(r.get('payment') is None for r in rows),
                 'missing_dates':sum(r.get('date') is None for r in rows),'questions':len(questions),
                 'asks_about_clipped_amounts':any(re.search('копе|обрез|не вид|непол|нечит',q,re.I) for q in questions),
                 'has_resolved_loan_name':bool(data.get('loan_name') or data.get('bank')),'explicit_unpaid_rows':sum(r.get('unpaid_confirmed') is True for r in rows),
                 'decimal_policy_confirmed':bool(data.get('decimal_policy_provenance'))})
print(json.dumps({'documents':docs,'active_bank_schedules':db.execute('SELECT count(*) FROM bank_schedules WHERE active=1').fetchone()[0],
                  'bank_payments':db.execute('SELECT count(*) FROM bank_payments').fetchone()[0],
                  'pending_bank_confirmations':db.execute('SELECT count(*) FROM local_pending WHERE action=? AND state=?',('bank_import','pending')).fetchone()[0]}))
db.close()
