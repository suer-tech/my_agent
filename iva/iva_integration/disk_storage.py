"""Disk tool: read-only analysis and source-bound, explicitly scoped cleanup."""
import fcntl
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = '/usr/local/sbin/iva-disk-storage'
PLAN_TTL = 1800


def present(value):
    """Supply consistent units; the model must not mix decimal GB with binary GiB."""
    value = dict(value)
    def size(amount, divisor=1024**3, unit='ГиБ'):
        return f'{amount / divisor:.2f}'.replace('.', ',') + ' ' + unit
    for key in ('disk', 'disk_before', 'disk_after'):
        if key not in value: continue
        disk = dict(value[key])
        disk['display'] = (f"Диск сервера: всего {size(disk['total_bytes'])}, "
                           f"занято {size(disk['used_bytes'])}, свободно {size(disk['free_bytes'])}; "
                           f"занято {disk['used_percent']}%.")
        value[key] = disk
    for key in ('candidates', 'directories'):
        if key in value:
            value[key] = [{**row, 'size_display': size(row['allocated_bytes'], 1024**2, 'МиБ')} for row in value[key]]
    for key in ('recoverable_bytes', 'removed_allocated_bytes'):
        if key in value: value[key.replace('_bytes', '_display')] = size(value[key], 1024**2, 'МиБ')
    value['display_instruction'] = 'Используйте готовые display/size_display для ответа. ГиБ=1024³ байт, МиБ=1024² байт; не смешивайте их с десятичными ГБ/МБ.'
    return value


def root_request(data):
    try:
        run = subprocess.run(['/usr/bin/sudo', '-n', HELPER], input=json.dumps(data),
                             text=True, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Проверка диска не завершилась; удаление без подтверждённого результата не повторяется') from None
    try: value = json.loads(run.stdout)
    except ValueError: raise RuntimeError('Проверка диска недоступна: проверьте установленный disk helper')
    if run.returncode or not isinstance(value, dict):
        raise RuntimeError(value.get('error', 'Проверка диска не завершилась'))
    return value


def permitted(text, rows):
    """The model chooses IDs, but cannot broaden the current owner's command."""
    body = text.lower().strip()
    if re.search(r'не\s+(?:удал|очист|чист|стира|трог)|\bкроме\b|исключ|\bесли\b|например|цитат|на\s+сайте|в\s+документ|науч|функцион|умел|возможност', body): return False
    command = re.search(r'\b(?:удали|удалить|очисти|очистить|почисти|почистить|убери|убрать|сотри|освободи)\b', body)
    if not command: return False
    prefix = body[:command.start()]
    if not re.fullmatch(r'(?:(?:ива|да|давай|пожалуйста|можешь|можно|нужно|надо|прошу|теперь|тогда)[\s,!.:—-]*)*', prefix): return False
    if '?' in body and not re.match(r'^(?:ива[, ]+)?можешь\b', body): return False
    if any(q in body for q in ('"', '«', '»', '“', '”', '\n>')): return False
    explicit_groups = set()
    if re.search(r'\bnpm\b|\bnpx\b', body): explicit_groups.add('npm')
    if re.search(r'\bpip\b', body): explicit_groups.add('pip')
    if re.search(r'node|заголовк', body): explicit_groups.add('node')
    if re.search(r'\bapt\b|скачанн\w*\s+пакет', body): explicit_groups.add('apt')
    if re.search(r'копи|бэкап|бекап|backup', body): explicit_groups.add('backups')
    general = bool(re.search(r'к[еэ]ш|мусор|ненужн|безопасн|предложенн|место\s+на\s+диске', body))
    for row in rows:
        if row['id'].lower() in body: continue
        if explicit_groups and row['group'] not in explicit_groups: return False
        if not explicit_groups and (not general or row['group'] == 'backups'): return False
        if 'root' in body and not row['id'].startswith('root_'): return False
        if re.search(r'только\s+(?:ив|пользовател)|пользователя\s+ива|у\s+ивы|к[еэ]ш\w*\s+ивы', body) and not row['id'].startswith('iva_'): return False
    return True


def execute(data, ledger=None, owner=None, source=None, *, state=None, runner=None):
    state = Path(state or ROOT / 'data/disk-storage')
    runner = runner or root_request
    action = data.get('action')
    if action == 'status': return present(runner({'action': 'status'}))
    if action not in ('analyze', 'clean'): raise ValueError('Неизвестное действие с диском')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan_file = state / 'plan.json'
        if action == 'analyze':
            result = present(runner({'action': 'analyze'}))
            if not result.get('ok'): return result
            plan = {'created': time.time(), 'candidates': result.get('candidates', [])}
            temporary = state / 'plan.tmp'; temporary.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            temporary.chmod(0o600); temporary.replace(plan_file)
            return {**result, 'plan_valid_seconds': PLAN_TTL,
                    'cleanup_instruction': 'Удаление только по прямой команде владельца. Передайте candidate_ids выбранных строк и полную точную цитату текущей команды instruction_quote. Произвольные пути не принимаются.'}
        from local_finance.received_income import raw_owner_input
        raw = raw_owner_input(ledger, owner, source or '') if ledger and owner else None
        if not raw or data.get('instruction_quote', '').strip() != raw['body'].strip():
            raise PermissionError('Нужно текущее прямое поручение владельца в Telegram и его точная цитата')
        ids = data.get('candidate_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 32 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Укажите конкретные candidate_ids из анализа')
        # Separate audit store: no financial tables or operations are modified.
        with closing(sqlite3.connect(state / 'audit.sqlite3')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS cleanups(source TEXT PRIMARY KEY, command_hash TEXT, ids TEXT, state TEXT, result TEXT, created REAL)')
            old = db.execute('SELECT command_hash,ids,state,result FROM cleanups WHERE source=?', (source,)).fetchone()
            encoded_ids = json.dumps(sorted(ids))
            if old:
                if old[0] != raw['hash'] or old[1] != encoded_ids: raise PermissionError('Повтор команды не может расширять набор удаления')
                if old[2] == 'done': return {**json.loads(old[3]), 'cached': True}
                return {'ok': False, 'error': 'Предыдущая очистка была прервана. Автоматического повтора удаления нет; выполните новый анализ.', 'needs_analysis': True}
            if not plan_file.exists(): raise ValueError('Сначала выполните анализ диска')
            plan = json.loads(plan_file.read_text(encoding='utf-8'))
            if time.time() - plan['created'] > PLAN_TTL: raise ValueError('Анализ устарел; проверьте диск заново')
            available = {r['id']: r for r in plan['candidates']}
            if any(i not in available for i in ids): raise ValueError('Кандидат отсутствует в последнем анализе')
            rows = [available[i] for i in ids]
            if any(not r.get('cleanable') for r in rows): raise ValueError('Выбранные данные используются или требуют ручного разбора')
            if not permitted(raw['body'], rows): raise PermissionError('Команда не разрешает удаление выбранных данных; запрос просмотра не является очисткой')
            db.execute('INSERT INTO cleanups VALUES(?,?,?,?,?,?)', (source, raw['hash'], encoded_ids, 'started', None, time.time())); db.commit()
            # A crash after this point stays uncertain and is never blindly replayed.
            try:
                result = present(runner({'action': 'clean', 'items': [{'id': r['id'], 'fingerprint': r['fingerprint']} for r in rows]}))
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                return {'ok': False, 'error': 'Очистка не подтверждена. Повторите анализ; автоматического повтора удаления нет.', 'needs_analysis': True}
            db.execute("UPDATE cleanups SET state='done',result=? WHERE source=?", (json.dumps(result, ensure_ascii=False), source)); db.commit()
            return result
