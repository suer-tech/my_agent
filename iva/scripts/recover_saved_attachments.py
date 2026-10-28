"""Recover already received file jobs. No polling and no direct Telegram sends."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger,Assistant
from local_finance.codex_cli import Codex
from local_finance.bot import best_photo


def main():
    l=Ledger(ROOT/'data/local-finance.sqlite3')
    try:
        Assistant(l,Codex());groups={};restored=0
        with l.transaction():
            for row in l.rows('SELECT * FROM local_telegram_messages ORDER BY id'):
                msg=json.loads(row['message_json'])
                if not msg.get('photo'):continue
                old=l.rows('SELECT metadata FROM local_updates WHERE id=?',(row['id'],))
                meta=json.loads(old[0]['metadata'] or '{}') if old else {}
                meta.update({k:best_photo(msg['photo']).get(k) for k in ('file_id','file_unique_id','file_size')})
                meta.update(kind='image',caption=msg.get('caption',''),media_group_id=msg.get('media_group_id'),received_at=0)
                l.db.execute('UPDATE local_updates SET metadata=? WHERE id=?',(json.dumps(meta,ensure_ascii=False),row['id']))
                if msg.get('media_group_id'):groups.setdefault(msg['media_group_id'],[]).append(row['id'])
            for ids in groups.values():
                if len(ids)<2:continue
                leader=min(ids)
                # Old individual extraction was incomplete; one combined user-requested retry.
                for uid in ids:l.db.execute('UPDATE local_updates SET state=? WHERE id=?',('pending' if uid==leader else 'grouped',uid))
                restored+=1
            for row in l.rows('SELECT * FROM local_updates WHERE text=?',('/incoming_pdf',)):
                metadata=json.loads(row['metadata'] or '{}')
                already=l.rows('SELECT id FROM bank_schedules WHERE document_id=?',(metadata.get('document_id',''),))
                if not already:
                    l.db.execute("UPDATE local_updates SET state='pending' WHERE id=?",(row['id'],));restored+=1
        print(json.dumps({'recovered_jobs':restored,'albums':len(groups),'photos':sum(len(v) for v in groups.values()),'financial_changes':0,'telegram_calls':0}))
    finally:l.db.close()


if __name__=='__main__':main()
