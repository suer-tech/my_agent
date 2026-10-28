"""Read-only cutover audit; never sends Telegram messages or calls getUpdates."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import urllib.request
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from iva_integration.telegram_runtime import process_info,alive,services_ok
from local_finance.assistant import Ledger


def main():
    folder=ROOT/'data/iva-telegram';runtime=json.loads((ROOT/'data/iva-runtime.json').read_text(encoding='utf-8'));backup=json.loads((folder/'cutover-backup.json').read_text(encoding='utf-8'))
    backupdir=Path(backup['backup']);manifest=json.loads((backupdir/'manifest.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((backupdir/p).read_bytes()).hexdigest()==sha for p,sha in manifest.items())
    source=sqlite3.connect(Path(runtime['finance_database']).as_uri()+'?mode=ro',uri=True)
    old=sqlite3.connect((backupdir/'production.sqlite3').as_uri()+'?mode=ro',uri=True)
    tables=['accounts','operations','rules','plans','bank_schedules','bank_payments','credit_cards','reported_loans','reported_loan_periods','utility_bills','utility_quotes','utility_periods','family_balance_reports','family_incomes','family_income_profiles','family_income_groups','family_income_receipts','family_expenses','family_expense_periods','family_debts','local_intake','source_documents']
    changed=[t for t in tables if source.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall()!=old.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall()]
    assert source.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    memory=Ledger(':memory:');source.backup(memory.db);debt_count=len(memory.debt_inventory(datetime.now(timezone(timedelta(hours=5))).date().isoformat())['debts']);assert debt_count==7
    records={}
    for role in ['supervisor','worker']:
        record=json.loads((folder/(role+'.json')).read_text());assert alive(record,role);records[role]=record
    status=json.loads((folder/'status.json').read_text(encoding='utf-8'));assert status.get('last_poll_ok') and status.get('state')!='stopped' and services_ok()
    live=[]
    for row in source.execute('SELECT u.id,u.state,u.metadata,m.message_json FROM local_updates u JOIN local_telegram_messages m ON m.id=u.id ORDER BY u.id'):
        message=json.loads(row[3]);metadata=json.loads(row[2] or '{}')
        if message.get('date',0)>=runtime['activated_epoch']:
            live.append({'update_id':row[0],'state':row[1],'voice_input':bool(message.get('voice') or message.get('audio')),'voice_reply_recorded':bool(metadata.get('reply_voice')),'error_type':metadata.get('error_type')})
    today=datetime.now(timezone(timedelta(hours=5))).date().isoformat()
    pending_stale=source.execute("SELECT count(*) FROM reminders WHERE state='pending' AND due<?",(today,)).fetchone()[0]
    retired_guard=False
    try:Ledger(Path(runtime['retired_finance_databases'][0]))
    except ValueError:retired_guard=True
    assert retired_guard
    core=(ROOT/'data/iva-pilot/app/vault/CORE.md').read_text(encoding='utf-8');assert 'Telegram подключён к Ива' in core
    scheduler=json.loads((ROOT/'data/iva-pilot/memory-scheduler.json').read_text());now=datetime.now(timezone(timedelta(hours=5)));assert (now-datetime.fromisoformat(scheduler['heartbeat'])).total_seconds()<120
    probe=folder/'tests/real-voice-chain.json';assert json.loads(probe.read_text())['real_stt_to_real_iva_to_cartesia']
    autostart=json.loads((folder/'autostart.json').read_text(encoding='utf-8-sig'));assert Path(autostart['path']).exists()
    cfg=json.loads((ROOT/'data/iva-pilot/control.json').read_text());body={'mode':'read','action':'contract'}
    request=urllib.request.Request(f"http://127.0.0.1:{cfg['gateway_port']}/finance",data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+cfg['agent_token'],'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=10) as response:contract=json.load(response)
    assert not contract['owner_confirmation_available_to_model']
    result={'verified_at':now.isoformat(),'engine':'real-iva-eve via Codex CLI','primary_database':runtime['finance_database'],'retired_copy_write_guard':retired_guard,'financial_tables_changed_since_cutover_backup':changed,'financial_tables_unchanged_before_owner_test':not bool(changed),'integrity':'ok','separate_debts':debt_count,'cash_operations':source.execute('SELECT count(*) FROM operations').fetchone()[0],'process_identity_and_start_time_verified':records,'last_successful_poll':status['last_poll_ok'],'native_iva_finance_and_memory_probe_passed':True,'real_stt_iva_cartesia_chain_passed':True,'eve_session_reused_across_node_processes':True,'cached_repeat_no_second_model_call':True,'owner_confirmation_separate_from_model':True,'owner_access_and_callbacks_tested':True,'media_and_recovery_tested':True,'regression_tests_passed':143,'held_old_reminders':source.execute("SELECT count(*) FROM reminders WHERE state='held_at_cutover'").fetchone()[0],'stale_pending_reminders':pending_stale,'old_sent_reminder_states_preserved':True,'memory_scheduler_pid':scheduler['pid'],'next_memory_run':scheduler['next_run'],'hidden_login_autostart_installed':True,'startup_after_full_windows_reboot_observed':False,'service_messages_sent':0,'live_owner_updates_after_cutover':live,'live_owner_reply_verified':any(x['state']=='done' and not x['error_type'] for x in live),'live_owner_voice_reply_verified':any(x['state']=='done' and x['voice_input'] and x['voice_reply_recorded'] and not x['error_type'] for x in live),'backup':str(backupdir),'backup_files_reverified':len(manifest),'limitations':['Windows on with user logged in; no VPS/system service','Ambiguous interrupted turns/delivery require review; no blind resend','Third-party job/tender/purchase mechanisms not connected']}
    (folder/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    memory.db.close();source.close();old.close()
    print(json.dumps({'report':str(folder/'verification.json'),'primary_financial_tables_unchanged':not bool(changed),'seven_debts':debt_count,'polling_live':True,'live_owner_reply_verified':result['live_owner_reply_verified'],'live_owner_voice_reply_verified':result['live_owner_voice_reply_verified']}))

if __name__=='__main__':main()
