"""Durable owner notices about positively verified automatic recoveries.

Collectors read production evidence; only this module's own database is written.
An ambiguous Telegram delivery is never retried automatically. Source cursors and
outbox entries commit together, so a crash cannot silently skip a recovery.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import argparse
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

ROOT = Path('/opt/iva')
DIRECTORY = ROOT / 'data/recovery-notices'
MAX_JSON = 1024 * 1024
MAX_AGE = 86400
RETENTION = 90 * 86400
LOCAL = timezone(timedelta(hours=5))
SETUP_ID = 'system:recovery_notices_enabled:v1'
SETUP_TEXT = ('Ива: включила краткие уведомления о подтверждённых исправлениях. '
              'Буду сообщать, что сломалось, что удалось восстановить и какой проверкой это подтверждено. '
              'Повторные попытки и старые события рассылаться не будут.')
RESEARCH_CODES = {
    'research_timeout': 'превышено время ответа исследователя',
    'research_auth': 'была недоступна авторизация исследователя',
    'research_quota': 'исследование упёрлось в ограничение сервиса',
    'research_validation': 'результат исследователя не прошёл проверку',
    'research_export': 'не удалось сохранить итог исследования',
    'research_storage': 'не хватало места для исследования',
    'research_sources': 'не удалось получить необходимые источники',
    'research_resources': 'исследованию не хватало ресурсов',
    'research_technical': 'исследование прервалось из-за технического сбоя',
}


def encoded(value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(text.encode('utf-8')) > MAX_JSON:
        raise ValueError('notice_data_too_large')
    return text


def message(event):
    """Only fixed templates; external error strings never enter owner messages."""
    source, code, action = (event.get(k) for k in ('source', 'code', 'action'))
    proof = event.get('proof', {})
    if (source, code, action) == ('threads', 'messages', 'code_patch'):
        body = ('Threads: проверка личных сообщений давала сбой. Исправление кода установлено; '
                'тесты и повторная проверка входящих, запросов и скрытых запросов прошли.')
    elif (source, code, action) == ('mail', 'mail_read_failed', 'retry'):
        body = ('Почта: чтение новых писем прерывалось. Повторное чтение прошло успешно; '
                'обработка продвинулась к следующим письмам.')
    elif source == 'research' and action == 'quote_correction' and code in {
            'agent_child_quote_not_found', 'agent_child_quote_too_long', 'agent_child_sources_not_read'}:
        body = ('Исследование: запрос дополнительной проверки не прошёл контроль источников и цитаты. '
                'Запрос исправлен: дословная цитата сверена с прочитанным источником, '
                'оркестратор одобрил проверку. '
                'Это исправление отдельного этапа.')
    elif (source, code, action) == ('research', 'research_scope_plan_rejected', 'plan_correction'):
        body = ('Исследование: план выходил за рамки выбранного направления. План исправлен '
                'и прошёл независимую проверку. Это исправление этапа планирования.')
    elif source == 'research' and action == 'retry' and code in RESEARCH_CODES:
        ending = ('Повторная обработка этого цикла завершилась без технических ошибок.'
                  if proof.get('same_cycle') is True else
                  'Следующий цикл завершился без технических ошибок; это подтверждает восстановление работы, '
                  'но не исправление старого отчёта.')
        body = 'Исследование: ' + RESEARCH_CODES[code] + '. ' + ending
    elif (source, code, action) == ('opencode', 'provider_unavailable', 'service_restart'):
        body = ('OpenCode: сервис моделей возвращал ошибку доступности. Сервис автоматически '
                'перезапущен; проверка его состояния и доступа к провайдеру прошла.')
    else:
        raise ValueError('unsupported_recovery_event')
    stamp = datetime.fromtimestamp(event['verified_at'], LOCAL).strftime('%d.%m %H:%M')
    return body + '\nПодтверждено: ' + stamp + ' (Екатеринбург).'


def _proof_valid(event):
    """Defense in depth: every factual clause requires bounded explicit proof."""
    source, code, action = (event.get(key) for key in ('source', 'code', 'action'))
    proof = event.get('proof')
    if not isinstance(proof, dict):
        return False
    def identity(key, empty=False):
        value = proof.get(key)
        return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{%d,100}' % (0 if empty else 1), value))
    def stamp(key):
        value = proof.get(key)
        return type(value) in (int, float) and math.isfinite(value) and 0 < value <= event['verified_at']
    def flags(*keys):
        return all(proof.get(key) is True for key in keys)
    def sequence(*keys):
        values = [proof.get(key) for key in keys]
        return (all(type(value) is int and value > 0 for value in values)
                and all(a < b for a, b in zip(values, values[1:])))
    if (source, code, action) == ('threads', 'messages', 'code_patch'):
        allowed = {'job_id','installed','tests_passed','installed_at','inbox_checked_at','inbox_finished_at',
                   'all_folders_checked','latest_incident_job','incident_inactive'}
        # The read may be a newer successful scan than the original verification.
        times = [proof.get(key) for key in ('installed_at','inbox_checked_at','inbox_finished_at')]
        return (set(proof) == allowed and identity('job_id') and stamp('installed_at')
                and proof['installed_at'] < event['verified_at']
                and all(type(value) in (int,float) and math.isfinite(value) and value > 0 for value in times)
                and times[0] < times[1] <= times[2]
                and event.get('campaign') in ('extension','development')
                and flags('installed','tests_passed','all_folders_checked','latest_incident_job','incident_inactive'))
    if (source, code, action) == ('mail', 'mail_read_failed', 'retry'):
        return (set(proof) == {'job_id','failed_run_id','verified_run_id','read_only','cursor_advanced','uidvalidity_unchanged'}
                and all(identity(key) for key in ('job_id','failed_run_id','verified_run_id'))
                and proof['failed_run_id'] != proof['verified_run_id']
                and flags('read_only','cursor_advanced','uidvalidity_unchanged'))
    if source == 'research' and action == 'quote_correction':
        return (set(proof) == {'cycle_id','task_id','failed_event_id','repair_event_id','review_event_id',
                              'literal_quote_verified','coordinator_approved','parent_task_completed','cycle_completed'}
                and identity('cycle_id') and identity('task_id')
                and sequence('failed_event_id','repair_event_id','review_event_id')
                and flags('literal_quote_verified','coordinator_approved','parent_task_completed')
                and proof.get('cycle_completed') is False)
    if (source, code, action) == ('research','research_scope_plan_rejected','plan_correction'):
        return (set(proof) == {'cycle_id','failed_event_id','verified_event_id','independent_scope_check','cycle_completed'}
                and identity('cycle_id') and sequence('failed_event_id','verified_event_id')
                and flags('independent_scope_check') and proof.get('cycle_completed') is False)
    if source == 'research' and action == 'retry' and code in RESEARCH_CODES:
        return (set(proof) == {'job_id','failed_run_id','verified_run_id','cycle_id','failed_cycle_id',
                              'same_cycle','terminal','technical_failures'}
                and all(identity(key) for key in ('job_id','failed_run_id','verified_run_id','cycle_id'))
                and identity('failed_cycle_id', empty=True)
                and proof['failed_run_id'] != proof['verified_run_id']
                and type(proof.get('same_cycle')) is bool
                and proof['same_cycle'] == (proof['cycle_id'] == proof['failed_cycle_id'])
                and flags('terminal') and type(proof.get('technical_failures')) is int
                and proof['technical_failures'] == 0)
    if (source, code, action) == ('opencode','provider_unavailable','service_restart'):
        return (set(proof) == {'attempted_at','restart_succeeded','global_healthy','provider_available','provider_503_confirmed'}
                and stamp('attempted_at') and flags('restart_succeeded','global_healthy','provider_available','provider_503_confirmed'))
    return False


def validate(event, now):
    if (not isinstance(event, dict) or not isinstance(event.get('id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', event['id'])):
        raise ValueError('invalid_recovery_id')
    stamp = event.get('verified_at')
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 < stamp <= now:
        raise ValueError('invalid_recovery_time')
    if not _proof_valid(event) or len(encoded(event).encode('utf-8')) > 8192:
        raise ValueError('invalid_recovery_proof')
    if (event.get('source') == 'threads'
            and event['proof']['inbox_finished_at'] > now):
        raise ValueError('invalid_recovery_proof')
    return message(event)


class Store:
    def __init__(self, directory, now=None):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'notices.sqlite3', timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notices(
                id TEXT PRIMARY KEY,source TEXT NOT NULL,code TEXT NOT NULL,action TEXT NOT NULL,
                verified_at REAL NOT NULL,proof TEXT NOT NULL,text TEXT NOT NULL,
                state TEXT NOT NULL,created REAL NOT NULL,message_id INTEGER,attempted_at REAL,delivered_at REAL);
        ''')
        # Caller holds the worker lock. A prior process may have sent before dying.
        with self.db:
            self.db.execute("UPDATE notices SET state='uncertain' WHERE state='sending'")

    def close(self):
        self.db.close()

    def cursor(self, source):
        row = self.db.execute('SELECT value FROM cursors WHERE source=?', (source,)).fetchone()
        return json.loads(row[0]) if row else None

    def commit_collection(self, source, result, now):
        state = encoded(result['state'])
        if not isinstance(result['state'], dict) or not isinstance(result['events'], list) or len(result['events']) > 1000:
            raise ValueError('invalid_collector_result')
        rows = [(event, validate(event, now)) for event in result['events']]
        with self.db:
            for event, text in rows:
                self.db.execute('''INSERT OR IGNORE INTO notices
                    (id,source,code,action,verified_at,proof,text,state,created) VALUES(?,?,?,?,?,?,?,?,?)''',
                    (event['id'], event['source'], event['code'], event['action'], event['verified_at'],
                     encoded(event['proof']), text, 'pending' if now - event['verified_at'] <= MAX_AGE else 'expired', now))
            self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)', (source, state))

    def setup_notice(self, now):
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO notices
                (id,source,code,action,verified_at,proof,text,state,created) VALUES(?,?,?,?,?,?,?,?,?)''',
                (SETUP_ID, 'system', 'notifications_enabled', 'configuration', now,
                 '{"configuration_verified":true}', SETUP_TEXT, 'pending', now))

    def maintain(self, now):
        with self.db:
            self.db.execute("UPDATE notices SET state='expired' WHERE state='pending' AND verified_at<?", (now - MAX_AGE,))
            # Compact old payloads, preserve identities forever to prevent replay.
            self.db.execute("UPDATE notices SET proof='{}',text='' WHERE state IN ('sent','expired') AND verified_at<? AND (proof!='{}' OR text!='')", (now - RETENTION,))

    def deliver(self, ready, send, now):
        row = self.db.execute("SELECT * FROM notices WHERE state='pending' ORDER BY created,id LIMIT 1").fetchone()
        if not row:
            return 'idle'
        try:
            if not ready():
                return 'preflight_failed'
        except Exception:
            return 'preflight_failed'
        with self.db:
            changed = self.db.execute("UPDATE notices SET state='sending',attempted_at=? WHERE id=? AND state='pending'", (now, row['id'])).rowcount
        if not changed:
            return 'not_claimed'
        try:
            result = send(row['text'])
            ident = result.get('message_id') if isinstance(result, dict) else None
            if type(ident) is not int or ident <= 0:
                raise ValueError('missing_telegram_ack')
        except Exception:
            with self.db:
                self.db.execute("UPDATE notices SET state='uncertain' WHERE id=?", (row['id'],))
            return 'uncertain'
        with self.db:
            self.db.execute("UPDATE notices SET state='sent',message_id=?,delivered_at=? WHERE id=?", (ident, now, row['id']))
        return 'sent'

    def status(self, now, collectors, delivery):
        counts = dict(self.db.execute('SELECT state,count(*) FROM notices GROUP BY state'))
        recent = [dict(row) for row in self.db.execute('''SELECT source,code,action,verified_at,state,text,message_id
            FROM notices ORDER BY verified_at DESC,id DESC LIMIT 12''')]
        return {'version': 1, 'checked_at': now, 'collectors': collectors, 'delivery': delivery,
                'counts': counts, 'recent': recent, 'policy': 'confirmed_recovery_only_no_uncertain_retries'}


def collectors(root=ROOT):
    from iva_integration.recovery_threads import collect as threads
    from iva_integration.recovery_schedules import collect as schedules
    from iva_integration.recovery_opencode import collect as opencode
    return {
        'threads': lambda state, now: threads(root / 'data/threads-browser', state, now),
        'schedules': lambda state, now: schedules(root / 'data/local-finance.sqlite3', root / 'data/demand-research.sqlite3', state, now),
        'opencode': lambda state, now: opencode(Path('/var/lib/iva-opencode-health-events/events.json'), state, now),
    }


def collect_all(store, readers, now):
    status = {}
    for name, reader in readers.items():
        try:
            result = reader(store.cursor(name), now)
            diagnostics = result.get('diagnostics', {})
            if (not isinstance(diagnostics, dict)
                    or any(key not in ('threads','schedules','research','opencode')
                           or value not in ('read_ok','source_unavailable','baseline','no_verified_repair','proof_unavailable')
                           for key, value in diagnostics.items())):
                raise ValueError('invalid_collector_diagnostics')
            store.commit_collection(name, result, now)
            status[name] = {'state': 'polled', 'eligible_events': len(result['events'])}
            if diagnostics:
                status[name]['diagnostics'] = diagnostics
        except Exception as error:
            # Do not expose source error contents or advance a failed cursor.
            status[name] = {'state': 'failed', 'error_type': type(error).__name__}
    return status


def save_status(directory, value):
    descriptor, name = tempfile.mkstemp(prefix='.status-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, Path(directory) / 'status.json')
    finally:
        Path(name).unlink(missing_ok=True)


def read_status(directory=DIRECTORY, now=None):
    try:
        path = Path(directory) / 'status.json'
        if path.stat().st_size > MAX_JSON:
            raise ValueError('oversized_status')
        value = json.loads(path.read_text(encoding='utf-8'))
        checked = value['checked_at']
        if not isinstance(value, dict) or value.get('version') != 1 or type(checked) not in (int, float) or not math.isfinite(checked):
            raise ValueError('invalid_status')
        value['stale'] = not 0 <= (time.time() if now is None else now) - checked <= 180
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return {'available': False, 'stale': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collect-only', action='store_true')
    parser.add_argument('--setup-notice', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix' or Path.cwd() != ROOT:
        raise RuntimeError('run_on_iva_vps')
    import fcntl
    os.umask(0o077)
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    with (DIRECTORY / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"busy"}'); return
        now = time.time()
        with closing(Store(DIRECTORY)) as store:
            polled = collect_all(store, collectors(), now)
            if args.setup_notice:
                store.setup_notice(now)
            store.maintain(now)
            delivery = 'collect_only'
            if not args.collect_only:
                try:
                    from iva_integration.run_pilot import env
                    from local_finance.bot import Telegram, OWNER, load_env
                    os.environ.update(env()); load_env(ROOT / '.env')
                    if os.environ.get('FINANCE_ALLOWED_USER_ID') != str(OWNER) or not os.environ.get('TELEGRAM_HTTPS_PROXY'):
                        raise RuntimeError('owner_transport_unavailable')
                    telegram = Telegram(os.environ['TELEGRAM_BOT_TOKEN'])
                    delivery = store.deliver(lambda: bool(telegram.call('getMe', {}).get('id')),
                                             lambda text: telegram.send(OWNER, text), now)
                except Exception:
                    delivery = 'transport_unavailable'
            value = store.status(time.time(), polled, delivery)
            save_status(DIRECTORY, value)
            print(json.dumps({'delivery': delivery, 'counts': value['counts'], 'collectors': polled}))


if __name__ == '__main__':
    main()
