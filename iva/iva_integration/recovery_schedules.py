"""Read-only, checkpointed proof collection for scheduled-work recovery notices.

This module never sends messages or changes either source database. The caller
must atomically persist the returned state and events in its own durable outbox.
Historical successes are baselined; an observed failure needs a later concrete
success. A successful intermediate research step is never recovery evidence.
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from iva_integration.scheduled_tasks import (
    _research_completed_without_technical_failure,
    _research_failure_group,
    _research_technical_issue,
)

LIMIT = 500
QUOTE_CODES = frozenset(('agent_child_quote_not_found', 'agent_child_quote_too_long',
                         'agent_child_sources_not_read'))


def _object(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _identity(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', value) else ''


def _hash(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _epoch(value, now):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return min(parsed.timestamp(), now)
    except (TypeError, ValueError):
        pass
    return now


def _connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def _valid_state(kind, state):
    """A malformed checkpoint is a fresh silent baseline, never replay input."""
    if not isinstance(state, dict) or type(state.get('watermark')) is not int or state['watermark'] < 0:
        return False
    if not isinstance(state.get('anchor'), str):
        return False
    if kind == 'schedules':
        if (not isinstance(state.get('pending'), list) or
                any(type(item) is not int or item < 1 for item in state['pending']) or
                not isinstance(state.get('episodes'), dict) or not isinstance(state.get('mail_cursors'), dict)):
            return False
        for key, episode in state['episodes'].items():
            if (not _identity(key) or not isinstance(episode, dict) or
                    not all(isinstance(episode.get(field), str) for field in ('id', 'code', 'run_id', 'cycle_id', 'kind')) or
                    not isinstance(episode.get('cursor'), dict) or
                    (episode['cursor'] and not _cursor(episode['cursor']))):
                return False
        return all(_identity(key) and isinstance(value, dict) and (not value or _cursor(value))
                   for key, value in state['mail_cursors'].items())
    if not isinstance(state.get('quotes'), dict) or not isinstance(state.get('plans'), dict) or not isinstance(state.get('emitted'), list):
        return False
    for value in state['quotes'].values():
        if (not isinstance(value, dict) or
                not all(isinstance(value.get(field), str) for field in ('id', 'code', 'cycle_id', 'task_id', 'key_hash')) or
                type(value.get('first_event')) is not int or
                ('repaired_event' in value and type(value['repaired_event']) is not int)):
            return False
    return (all(isinstance(value, dict) and isinstance(value.get('id'), str) and isinstance(value.get('code'), str)
                and type(value.get('first_event')) is int for value in state['plans'].values()) and
            all(isinstance(value, str) for value in state['emitted']))


def _cursor(value):
    value = _object(value)
    uid = value.get('last_uid')
    validity = value.get('uidvalidity')
    if isinstance(validity, (str, int)) and len(str(validity)) <= 30 and str(validity).isdigit() and type(uid) is int and uid >= 0:
        return {'uidvalidity': str(validity), 'last_uid': uid}
    return {}


def _event(episode, source, action, now, proof):
    return {'id': 'scheduled-recovery:' + episode['id'], 'source': source,
            'code': episode['code'], 'action': action, 'verified_at': now, 'proof': proof}


def _research_failure(result):
    technical = _research_technical_issue(result) if result.get('ok') is True and not result.get('has_more') else None
    if result.get('ok') is False or technical:
        effective = technical or result
        group, _ = _research_failure_group(effective)
        # Planned lease/model waits are successful checkpoints, not failures.
        return 'research_' + group
    return None


def _episode(job, run, result, cursor):
    kind = _object(job['spec']).get('kind')
    code = 'mail_read_failed' if kind == 'mail_watch' else _research_failure(result)
    if not code:
        return None
    return {'id': _hash(job['id'], run['id'], code), 'code': code,
            'run_id': _identity(run['id']), 'cycle_id': _identity(result.get('cycle_id')),
            'kind': kind, 'cursor': cursor}


def _schedule_baseline(db, jobs):
    latest = db.execute('SELECT rowid,id FROM scheduled_runs ORDER BY rowid DESC LIMIT 1').fetchone()
    state = {'watermark': latest[0] if latest else 0, 'anchor': latest[1] if latest else '',
             'pending': [row[0] for row in db.execute("SELECT rowid FROM scheduled_runs WHERE state='running'")],
             'episodes': {}, 'mail_cursors': {}}
    for job in jobs.values():
        kind = _object(job['spec']).get('kind')
        if kind == 'mail_watch':
            state['mail_cursors'][job['id']] = _cursor(job['cursor'])
        if kind not in ('mail_watch', 'demand_research') or not job['error']:
            continue
        row = db.execute("SELECT * FROM scheduled_runs WHERE job_id=? AND finished IS NOT NULL ORDER BY rowid DESC LIMIT 1", (job['id'],)).fetchone()
        if not row:
            continue
        result = _object(row['result'])
        episode = _episode(job, row, result, _cursor(job['cursor']))
        # A good research checkpoint can retain an existing incident. Its
        # first failed run remains in the scheduler's durable notice cursor.
        if kind == 'demand_research' and not episode:
            notices = _object(_object(job['cursor']).get('demand_notice_state')).get('notices', {})
            if isinstance(notices, dict):
                for notice in notices.values():
                    run_id = _identity(_object(notice).get('run_id'))
                    prior = db.execute('SELECT * FROM scheduled_runs WHERE id=? AND job_id=?', (run_id, job['id'])).fetchone()
                    if prior:
                        episode = _episode(job, prior, _object(prior['result']), {})
                        if episode:
                            break
        if episode:
            state['episodes'][job['id']] = episode
    return state


def _schedules(path, state, now):
    with closing(_connect(path)) as db:
        jobs = {row['id']: dict(row) for row in db.execute('SELECT id,spec,state,cursor,error FROM scheduled_jobs')}
        if state is None:
            return _schedule_baseline(db, jobs), []
        maximum = db.execute('SELECT coalesce(max(rowid),0) FROM scheduled_runs').fetchone()[0]
        anchor = db.execute('SELECT id FROM scheduled_runs WHERE rowid=?', (state['watermark'],)).fetchone()
        if maximum < state['watermark'] or (state['watermark'] and (not anchor or anchor[0] != state['anchor'])):
            # A restored source snapshot cannot prove a new recovery.
            return _schedule_baseline(db, jobs), []
        pending = sorted(set(state.get('pending', [])))[:LIMIT]
        placeholders = ','.join('?' for _ in pending) or 'NULL'
        rows = db.execute(f'SELECT rowid AS sequence,* FROM scheduled_runs WHERE rowid>? OR rowid IN ({placeholders}) ORDER BY rowid LIMIT ?',
                          (state.get('watermark', 0), *pending, LIMIT)).fetchall()
        events = []
        episodes = state.setdefault('episodes', {})
        cursors = state.setdefault('mail_cursors', {})
        newest = {row['job_id']: row['sequence'] for row in db.execute(
            'SELECT job_id,max(rowid) AS sequence FROM scheduled_runs GROUP BY job_id')}
        for row in rows:
            sequence = row['sequence']
            if sequence > state['watermark']:
                state['watermark'], state['anchor'] = sequence, row['id']
            if row['state'] == 'running':
                if sequence not in pending:
                    pending.append(sequence)
                continue
            if sequence in pending:
                pending.remove(sequence)
            job = jobs.get(row['job_id'])
            if not job or job['state'] in ('paused', 'cancelled') or not row['finished']:
                continue
            kind = _object(job['spec']).get('kind')
            if kind not in ('mail_watch', 'demand_research'):
                continue
            result = _object(row['result'])
            if not result or row['state'] == 'cancelled':
                continue
            job_id = job['id']
            episode = episodes.get(job_id)
            failed = result.get('ok') is False if kind == 'mail_watch' else bool(_research_failure(result))
            if failed:
                if not episode:
                    episodes[job_id] = _episode(job, row, result, cursors.get(job_id, {}))
                continue
            if kind == 'mail_watch':
                updated = _cursor(result.get('cursor'))
                before = episode.get('cursor', {}) if episode else {}
                if episode and result.get('ok') is True and (result.get('baseline') or (
                        before and updated and updated['uidvalidity'] != before['uidvalidity'])):
                    # A cursor reset skips history; even a later good read
                    # cannot prove the previous failed message was processed.
                    episodes.pop(job_id, None)
                    episode = None
                recovered = (result.get('ok') is True and not result.get('baseline') and result.get('read_only') is True
                             and updated and before and updated['uidvalidity'] == before['uidvalidity']
                             and updated['last_uid'] > before['last_uid'])
                if result.get('ok') is True and updated:
                    cursors[job_id] = updated
                    if episode and not before:
                        episode['cursor'] = updated
                proof = {'job_id': _identity(job_id), 'failed_run_id': episode.get('run_id', '') if episode else '',
                         'verified_run_id': _identity(row['id']), 'read_only': True, 'cursor_advanced': True,
                         'uidvalidity_unchanged': True}
            else:
                cycle = _identity(result.get('cycle_id'))
                # Require the real final-result shape. Retention summaries and
                # generic ok checkpoints lack these gates and cannot verify it.
                recovered = (result.get('has_more') is False and result.get('stage') == 'done' and bool(cycle)
                             and isinstance(result.get('coverage'), dict)
                             and _research_completed_without_technical_failure(result))
                proof = {'job_id': _identity(job_id), 'failed_run_id': episode.get('run_id', '') if episode else '',
                         'verified_run_id': _identity(row['id']), 'cycle_id': cycle,
                         'failed_cycle_id': episode.get('cycle_id', '') if episode else '',
                         'same_cycle': bool(episode and episode.get('cycle_id') == cycle),
                         'terminal': True, 'technical_failures': 0}
            if episode and recovered and newest.get(job_id) == sequence:
                # A missed poll may contain failure -> success -> new failure
                # or an unfinished newer attempt. Historical success must not
                # announce current recovery; retain the episode until verified.
                events.append(_event(episode, 'mail' if kind == 'mail_watch' else 'research', 'retry',
                                     _epoch(row['finished'], now), proof))
                episodes.pop(job_id, None)
        state['pending'] = pending
        # Pausing/removing a task is not a repair or permission to send a notice.
        for job_id in list(episodes):
            if job_id not in jobs or jobs[job_id]['state'] in ('paused', 'cancelled'):
                episodes.pop(job_id, None)
        return state, events


def _research_rows(db, state, rows, now, baseline=False):
    events = []
    quotes, plans = state.setdefault('quotes', {}), state.setdefault('plans', {})
    emitted = state.setdefault('emitted', [])
    for row in rows:
        if row['id'] > state['watermark']:
            state['watermark'] = row['id']
            state['anchor'] = _hash(row['id'], row['cycle_id'], row['event'], row['created'])
        cycle = _identity(row['cycle_id'])
        if not cycle:
            continue
        detail = _object(row['detail'])
        event = row['event']
        if event == 'child_quote_repair':
            task, request = _identity(detail.get('task_id')), _identity(detail.get('request_hash'))
            if not task or not request or not isinstance(detail.get('key'), str):
                continue
            identity = _hash(cycle, task, request)
            if detail.get('outcome') == 'started' and detail.get('category') in QUOTE_CODES:
                quotes.setdefault(identity, {'id': identity, 'code': detail['category'], 'cycle_id': cycle,
                                             'task_id': task, 'key_hash': _hash(detail['key']), 'first_event': row['id']})
            elif detail.get('outcome') == 'repaired' and identity in quotes:
                quotes[identity]['repaired_event'] = row['id']
            elif detail.get('outcome') == 'unsupported':
                quotes.pop(identity, None)
        elif event == 'review_completed':
            task = _identity(detail.get('task_id'))
            accepted = {_hash(item.get('key')) for item in _object(detail.get('review')).get('decisions', [])
                        if isinstance(item, dict) and item.get('decision') == 'approve'}
            parent = db.execute('SELECT status FROM demand_agent_tasks WHERE id=? AND cycle_id=?', (task, cycle)).fetchone()
            for identity, episode in list(quotes.items()):
                if episode['cycle_id'] != cycle or episode['task_id'] != task:
                    continue
                if (parent and parent['status'] == 'done' and episode.get('repaired_event')
                        and episode['key_hash'] in accepted):
                    coalesced = _hash(cycle, episode['code'])
                    if not baseline and coalesced not in emitted:
                        events.append(_event(episode, 'research', 'quote_correction', _epoch(row['created'], now),
                                             {'cycle_id': cycle, 'task_id': task, 'failed_event_id': episode['first_event'],
                                              'repair_event_id': episode['repaired_event'], 'review_event_id': row['id'],
                                              'literal_quote_verified': True, 'coordinator_approved': True,
                                              'parent_task_completed': True, 'cycle_completed': False}))
                    emitted.append(coalesced)
                quotes.pop(identity, None)
        elif event == 'scope_plan_rejected':
            plans.setdefault(cycle, {'id': _hash(cycle, 'scope_plan'), 'code': 'research_scope_plan_rejected',
                                     'first_event': row['id']})
        elif event == 'scope_plan_checked':
            decisions = detail.get('decisions')
            valid = (detail.get('limitations') == [] and isinstance(decisions, list) and bool(decisions)
                     and all(isinstance(item, dict) and item.get('relationship') in ('same_problem', 'connected_pivot') for item in decisions))
            episode = plans.get(cycle)
            if episode and valid:
                if not baseline:
                    events.append(_event(episode, 'research', 'plan_correction', _epoch(row['created'], now),
                                         {'cycle_id': cycle, 'failed_event_id': episode['first_event'],
                                          'verified_event_id': row['id'], 'independent_scope_check': True,
                                          'cycle_completed': False}))
                plans.pop(cycle, None)
    state['emitted'] = list(dict.fromkeys(emitted))[-512:]
    return events


def _research(path, state, now):
    with closing(_connect(path)) as db:
        if state is not None:
            anchor = db.execute('SELECT id,cycle_id,event,created FROM demand_events WHERE id=?', (state['watermark'],)).fetchone()
            sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='demand_events'").fetchone()
            # Pruning retains AUTOINCREMENT's high-water mark; an actual DB
            # rollback or divergent event at the same ID invalidates evidence.
            if ((sequence and sequence[0] < state['watermark']) or
                    (anchor and _hash(*anchor) != state['anchor'])):
                state = None
        if state is None:
            latest = db.execute('SELECT id,cycle_id,event,created FROM demand_events ORDER BY id DESC LIMIT 1').fetchone()
            state = {'watermark': latest[0] if latest else 0, 'anchor': _hash(*latest) if latest else '',
                     'quotes': {}, 'plans': {}, 'emitted': []}
            # Reconstruct only in-flight work. Old completed repairs must not
            # turn into a backlog of owner messages on first installation.
            rows = db.execute("SELECT e.* FROM demand_events e JOIN demand_cycles c ON c.id=e.cycle_id WHERE c.state='running' AND e.event IN ('child_quote_repair','review_completed','scope_plan_rejected','scope_plan_checked') ORDER BY e.id").fetchall()
            _research_rows(db, state, rows, now, baseline=True)
            return state, []
        rows = db.execute("SELECT * FROM demand_events WHERE id>? ORDER BY id LIMIT ?", (state.get('watermark', 0), LIMIT)).fetchall()
        events = _research_rows(db, state, rows, now)
        # Drop unresolved metadata only after catching up. Pruning halfway
        # through a batch could discard a quote before its later review event.
        maximum = db.execute('SELECT coalesce(max(id),0) FROM demand_events').fetchone()[0]
        if state.get('watermark', 0) >= maximum:
            active = {row[0] for row in db.execute("SELECT id FROM demand_cycles WHERE state='running'")}
            state['quotes'] = {key: value for key, value in state.get('quotes', {}).items() if value['cycle_id'] in active}
            state['plans'] = {key: value for key, value in state.get('plans', {}).items() if key in active}
        return state, events


def collect(finance_path: Path, research_path: Path, previous: dict | None, now: float):
    """Return JSON state + proof-only events; source outages leave cursors intact."""
    state = json.loads(json.dumps(previous)) if isinstance(previous, dict) and previous.get('version') == 1 else {'version': 1}
    events = []
    diagnostics = {}
    for key, path, reader in (('schedules', finance_path, _schedules), ('research', research_path, _research)):
        try:
            # A read may fail after processing several rows. Commit its
            # watermark only together with all resulting proof events.
            source_state = json.loads(json.dumps(state[key])) if _valid_state(key, state.get(key)) else None
            updated, collected = reader(path, source_state, now)
        except (OSError, sqlite3.Error):
            diagnostics[key] = 'source_unavailable'
            continue
        state[key] = updated
        events.extend(collected)
        diagnostics[key] = 'read_ok'
    return {'state': state, 'events': events, 'diagnostics': diagnostics}
