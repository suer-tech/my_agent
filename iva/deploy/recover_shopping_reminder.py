"""Recover the owner's explicitly requested 2026-10-21 shopping reminder edit.

Defaults to a private temporary database copy. --apply targets the live VPS only.
No Telegram message or synthetic Telegram authorization is created.
"""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_finance.personal_calendar import Calendar, now

EVENT_ID = '5aef8760eb2226670bdb'
OLD_TITLE = 'Купить термоголовку регулятора на тёплый пол'
NEW_TITLE = OLD_TITLE + ' и фильтры для воды'
SOURCE = 'owner-recovery:calendar-shopping-2026-10-21:608703940'


def snapshot(source, target):
    with sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def recover(path):
    store = Calendar(path)
    try:
        before = store.rows('SELECT * FROM personal_events ORDER BY id')
        event = next(row for row in before if row['id'] == EVENT_ID)
        assert event['title'] in (OLD_TITLE, NEW_TITLE) and event['active'] == 1, 'Unexpected reminder state'
        operations = store.rows('SELECT * FROM operations ORDER BY id')
        result = store.execute({'action': 'update', 'id': EVENT_ID, 'title': NEW_TITLE}, SOURCE)
        assert result['ok']
        with store.transaction():
            store.expand(now() + timedelta(days=7))
        after = store.rows('SELECT * FROM personal_events ORDER BY id')
        expected = [{**row, 'title': NEW_TITLE} if row['id'] == EVENT_ID else row for row in before]
        assert after == expected, 'Reminder fields other than the requested title changed'
        assert operations == store.rows('SELECT * FROM operations ORDER BY id'), 'Financial operations changed'
        pending = store.rows("SELECT due,text FROM personal_notices WHERE event_id=? AND state='pending' ORDER BY due LIMIT 1", (EVENT_ID,))
        assert pending and 'фильтры для воды' in pending[0]['text']
        return {'ok': True, 'event': result['event'], 'next_pending_notice': pending[0],
                'scheduler': store.status(), 'only_requested_title_changed': True,
                'financial_operations_unchanged': True, 'duplicate': result.get('duplicate', False)}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    assert ROOT.as_posix() == '/opt/iva', 'Run this recovery on the authoritative VPS'
    live = ROOT / 'data/local-finance.sqlite3'
    release = ROOT / 'data/calendar-auth-release-20261021'
    if args.apply:
        backup = release / 'before-shopping-recovery.sqlite3'
        if not backup.exists():
            snapshot(live, backup)
            backup.chmod(0o600)
        report = recover(live)
        report['applied'] = True
        target = release / 'shopping-recovery.json'
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        target.chmod(0o600)
    else:
        with tempfile.TemporaryDirectory(prefix='shopping-recovery-') as tmp:
            copy = Path(tmp) / 'copy.sqlite3'
            snapshot(live, copy)
            report = recover(copy)
            assert recover(copy)['duplicate']
            report.update(applied=False, repeat_is_idempotent=True)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
