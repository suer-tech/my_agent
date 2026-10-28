"""Read-only local status; never polls, sends messages or prints credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_finance.bot import load_env,Telegram,OWNER
from local_finance.voice import Voice


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api',action='store_true',help='Only getMe/getWebhookInfo; no polling or messages')
    args = parser.parse_args()
    load_env(ROOT/'.env')
    secrets = [value for key,value in os.environ.items() if value and any(word in key.upper() for word in ('TOKEN','API_KEY','SECRET','PASSWORD'))]
    report = {'configured_owner_matches':os.environ.get('FINANCE_ALLOWED_USER_ID')==str(OWNER),'token_configured':bool(os.environ.get('TELEGRAM_BOT_TOKEN'))}
    report['reminders'] = {'default_lead_days':int(os.environ.get('FINANCE_DEFAULT_LEAD_DAYS','1')),'local_hour':int(os.environ.get('FINANCE_REMINDER_HOUR','9')),'timezone':'Asia/Yekaterinburg','check_interval_seconds':60}
    report['voice_runtime_ready'] = Voice().ready
    logs = {}
    for name in ('local-bot.stdout.log','local-bot.stderr.log'):
        path = ROOT/'data'/name
        if path.exists():
            text = path.read_text(encoding='utf-8',errors='replace')[-5000:]
            for secret in secrets: text = text.replace(secret,'[REDACTED]')
            logs[name] = re.sub(r'\d{5,}:[A-Za-z0-9_-]{15,}','[REDACTED]',text)
    report['logs_redacted'] = logs
    database = ROOT/'data'/'local-finance.sqlite3'
    if database.exists():
        db = sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            report['poll_offset_record_present'] = bool(db.execute("SELECT value FROM local_meta WHERE key='offset'").fetchall())
            report['update_states'] = db.execute('SELECT state,count(*) FROM local_updates GROUP BY state').fetchall()
            report['account_count'] = db.execute('SELECT count(*) FROM accounts').fetchone()[0]
            report['history_message_count'] = db.execute('SELECT count(*) FROM local_history').fetchone()[0]
            report['expense_rule_leads'] = db.execute("SELECT lead_days,count(*) FROM rules WHERE kind='expense' GROUP BY lead_days").fetchall()
            report['incoming_kinds'] = [('command' if row[0].startswith('/') else 'text',row[1]) for row in db.execute('SELECT text,state FROM local_updates ORDER BY id DESC LIMIT 5')]
            report['voice_updates'] = db.execute("SELECT state,count(*) FROM local_updates WHERE text='/incoming_voice' GROUP BY state").fetchall()
            voice_records = [(row[0],json.loads(row[1] or '{}')) for row in db.execute("SELECT state,metadata FROM local_updates WHERE text='/incoming_voice'")]
            report['voice_confirmed_sent_count'] = sum(state=='done' and bool(meta.get('reply_voice')) for state,meta in voice_records)
            report['voice_failed_count'] = sum(bool(meta.get('voice_failed')) for state,meta in voice_records)
            mode=db.execute("SELECT value FROM local_meta WHERE key='voice_mode'").fetchone()
            report['voice_mode']=mode[0] if mode else os.environ.get('FINANCE_VOICE_MODE','auto')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'credit_cards' in tables:
                report['credit_cards']=[{'name':r[0],'due_day':r[1],'first_due':r[2],'balance_known':r[3] is not None,'interest_only_reported':None if r[4] is None else bool(r[4]),'account_bound':r[5] is not None} for r in db.execute('SELECT name,due_day,first_due,balance,interest_only,account FROM credit_cards ORDER BY name')]
        finally: db.close()
    if args.api:
        try:
            api = Telegram(os.environ['TELEGRAM_BOT_TOKEN'])
            me = api.call('getMe',{})
            webhook = api.call('getWebhookInfo',{})
            report['telegram'] = {'ok':True,'username':me.get('username'),'name':me.get('first_name'),'webhook_set':bool(webhook.get('url')),'pending_updates':webhook.get('pending_update_count')}
        except (KeyError,RuntimeError): report['telegram'] = {'ok':False,'error':'API check failed; credentials are not printed'}
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__': main()
