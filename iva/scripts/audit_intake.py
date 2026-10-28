"""Private intake audit: classifications and counts only; no financial values/text."""
import json
from pathlib import Path
import re
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
db=sqlite3.connect((ROOT/'data/local-finance.sqlite3').as_uri()+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
updates=[]
for row in db.execute('SELECT * FROM local_updates ORDER BY id DESC LIMIT 8'):
    metadata=json.loads(row['metadata'] or '{}')
    text=metadata.get('transcript',row['text'])
    answer=row['answer'] or ''
    updates.append({'state':row['state'],'voice':row['text']=='/incoming_voice','monthly_or_planned':bool(re.search('план|ежемесяч',text,re.I)),
                    'answer_has_confirmation_code':'/confirm ' in answer,'answer_is_question':'?' in answer,
                    'answer_mentions_dates':bool(re.search('дат|числ|когда',answer,re.I)),
                    'answer_reports_error':bool(re.search('отклонён|завершился с ошибкой|превысил таймаут',answer,re.I))})
tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
drafts=[json.loads(r[0]) for r in db.execute('SELECT payload FROM local_intake')] if 'local_intake' in tables else []
intake_status={'preserved_original_message_count':db.execute('SELECT count(*) FROM local_intake_sources').fetchone()[0] if 'local_intake_sources' in tables else 0,
               'structured_item_count':sum(len(d.get('items',[])) for d in drafts),'basis_choices':[d.get('_basis_choice') for d in drafts if d.get('_basis_choice')],
               'unresolved_question_count':sum(len(d.get('questions',[])) for d in drafts)}
print(json.dumps({'intake':intake_status,'actual_operation_counts':dict(db.execute('SELECT kind,count(*) FROM operations WHERE void=0 GROUP BY kind').fetchall()),
                  'accounts':db.execute('SELECT count(*) FROM accounts').fetchone()[0],
                  'recurring_rules':db.execute('SELECT count(*) FROM rules').fetchone()[0],
                  'loan_conditions':db.execute('SELECT count(*) FROM loans').fetchone()[0],
                  'pending_actions':dict(db.execute("SELECT action,count(*) FROM local_pending WHERE state='pending' GROUP BY action").fetchall()),
                  'recent_update_classifications':updates},ensure_ascii=True,indent=2))
db.close()
