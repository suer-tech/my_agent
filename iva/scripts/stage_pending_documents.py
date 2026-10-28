"""Download queued originals, without polling or sending Telegram messages."""
import json
import os
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.bot import load_env,Telegram

load_env(ROOT/'.env')
db=sqlite3.connect(ROOT/'data/local-finance.sqlite3');db.row_factory=sqlite3.Row
api=Telegram(os.environ['TELEGRAM_BOT_TOKEN']);results=[]
for row in db.execute('SELECT id,text,metadata FROM local_updates WHERE text IN (?,?,?)',('/incoming_pdf','/incoming_image','/incoming_text')):
    path=ROOT/'data/documents/inbox'/f"{row['id']}.bin"
    if not path.exists():api.download_attachment(json.loads(row['metadata']),path)
    results.append({'kind':row['text'],'original_saved':path.exists()})
print(json.dumps({'queued_documents':len(results),'results':results}))
db.close()
