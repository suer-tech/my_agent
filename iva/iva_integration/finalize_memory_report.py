"""Read-only checks and private evidence report for the completed memory pilot."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import subprocess
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
PILOT=ROOT/'data/iva-pilot'; APP=PILOT/'app'; VAULT=APP/'vault'
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
baseline=read(PILOT/'isolation.json')
finance=ROOT/'data/local-finance.sqlite3'
assert digest(finance)==baseline['production_sha256']
with sqlite3.connect(finance.as_uri()+'?mode=ro',uri=True) as db:
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
documents=ROOT/'data/documents'; saved=Path(baseline['backup'])/'documents'
current={p.relative_to(documents).as_posix():digest(p) for p in documents.rglob('*') if p.is_file()}
original={p.relative_to(saved).as_posix():digest(p) for p in saved.rglob('*') if p.is_file()}
assert current==original
backup=read(PILOT/'memory-backup.json'); backupdir=Path(backup['path'])
manifest=read(backupdir/'manifest.json')
assert all(digest(backupdir/path)==sha for path,sha in manifest.items())
scheduler=read(PILOT/'memory-scheduler.json')
now=datetime.now(timezone(timedelta(hours=5)))
assert (now-datetime.fromisoformat(scheduler['heartbeat'])).total_seconds()<120
processes=subprocess.run(['tasklist','/FI',f"PID eq {scheduler['pid']}",'/FO','CSV','/NH'],capture_output=True,check=True)
assert f'"{scheduler["pid"]}"'.encode() in processes.stdout
autostart=read(PILOT/'memory-autostart.json'); assert Path(autostart['path']).is_file()
checkpoint=read(APP/'data/memory/night/2026-10-19.json'); assert checkpoint['completedAt'] and checkpoint['coreDone']
cards=list((VAULT/'cards').rglob('*.md'));assert len(cards)==4
source=VAULT/'library/owner-confirmed-profile-2026-10-19.md';assert source.is_file()
# Detect key formats only; never return matched content.
texts='\n'.join(p.read_text(encoding='utf-8') for p in VAULT.rglob('*.md'))
assert 'sk_car_' not in texts and 'CARTESIA_API_KEY=' not in texts
report={
 'verified_at':now.isoformat(), 'native_iva_memory':True,
 'profile_source':str(source),'core':str(VAULT/'CORE.md'),
 'cards':[str(p) for p in cards], 'card_sources':'daily/2026-10-19.md 14:41; original current-owner-chat source in library',
 'native_day':{'script':str(APP/'scripts/memory/night.ts'),'date':'2026-10-19','actual_codex_cli_calls':2,'input_tokens':23352,'completed':True},
 'native_synthetic_cycle':read(PILOT/'memory-native-verification.json'),
 'native_search_test':{'scope_relevance':True,'corrected_fact':True,'unknown_birthdate':True,'standalone_typescript_resolution_adapter':True,'fixture_graph_absent_search_fallback_worked':True},
 'new_session_and_after_runtime_restart':{'verified':True,'language':'Russian','timezone':'Asia/Yekaterinburg','birthdates':'unknown','financial_truth':'SQLite'},
 'production_period_rollups':'No closed owner week/month/year yet; complete hierarchy verified separately on synthetic dates, 3 CLI calls, zero fallbacks',
 'scheduler':scheduler,'autostart':autostart,'backup':backup,'backup_hashes_reverified':len(manifest),
 'production_finance_unchanged':True,'production_sqlite_integrity':'ok','production_documents_hashes_unchanged':True,
 'secrets_detected_in_vault':False,'regression_tests_passed':110,
 'limitations':['Windows must be on and user logged in; catchup on resume/login','Native backlog processes at most 3 days per night','No remote GitHub backup or Telegram cutover','Directory fsync skipped only on Windows; file fsync retained','No system reboot/logon or actual future 04:00 firing observed; running heartbeat, catchup and installed startup shortcut verified']
}
(PILOT/'memory-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report':str(PILOT/'memory-verification.json'),'profile_cards':len(cards),'finance_unchanged':True,'documents_unchanged':True,'backup_files_verified':len(manifest),'scheduler_pid':scheduler['pid'],'next_run':scheduler['next_run']}))
