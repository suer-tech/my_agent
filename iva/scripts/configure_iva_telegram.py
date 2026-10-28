"""Select the verified production ledger and hold pre-cutover work without replay."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'plugin/family-finance'))
from finance.core import Ledger


def main():
    report=json.loads((ROOT/'data/iva-telegram/cutover-backup.json').read_text(encoding='utf-8'))
    assert report['financial_semantics_equal'] and report['files_verified']>0
    path=ROOT/'data/iva-runtime.json'
    if path.exists() and json.loads(path.read_text()).get('mode')=='telegram':print('Authoritative Telegram runtime already configured');return
    now=datetime.now(timezone(timedelta(hours=5)));ledger=Ledger(ROOT/'data/local-finance.sqlite3')
    assert len(ledger.debt_inventory(now.date().isoformat())['debts'])==7
    with ledger.transaction():
        held_updates=ledger.db.execute("UPDATE local_updates SET state='held_before_cutover' WHERE state NOT IN ('done','done_group','held_before_cutover')").rowcount
        ledger.db.execute("UPDATE local_pending SET state='uncertain' WHERE state='processing'")
        ledger.queue_reminders(now.date().isoformat(),(now.date()+timedelta(days=366)).isoformat())
        held_reminders=ledger.db.execute("UPDATE reminders SET state='held_at_cutover' WHERE state='pending' AND due<=?",(now.date().isoformat(),)).rowcount
        ledger.audit('iva_telegram_cutover',{'primary_database':'data/local-finance.sqlite3','prior_pilot_retired':True,'held_legacy_updates':held_updates,'held_old_reminders':held_reminders,'previous_sent_reminders_preserved':True,'service_test_message_authorized':False})
    config={'mode':'telegram','engine':'real-iva-eve','finance_database':str(ROOT/'data/local-finance.sqlite3'),'retired_finance_databases':[str(ROOT/'data/iva-pilot/finance/finance.sqlite3')],'vault':str(ROOT/'data/iva-pilot/app/vault'),'allowed_owner':412850740,'activated_epoch':int(now.timestamp()),'activated_at':now.isoformat(),'reminders_timezone':'Asia/Yekaterinburg','reminders_hour':9,'service_message_enabled':False}
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'data/iva-pilot/finance/RETIRED.json').write_text(json.dumps({'primary_database':config['finance_database'],'retired_at':now.isoformat(),'use_for_live_writes':False},indent=2),encoding='utf-8')
    report.update(primary_database=config['finance_database'],pilot_retired_for_writes=True,held_old_updates=held_updates,held_old_reminders=held_reminders,activated_at=now.isoformat())
    (ROOT/'data/iva-telegram/cutover-backup.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    ledger.db.close();print(json.dumps({'primary_selected':True,'seven_debts_preserved':True,'old_updates_held':held_updates,'old_reminders_held':held_reminders,'messages_sent':0}))

if __name__=='__main__':main()
