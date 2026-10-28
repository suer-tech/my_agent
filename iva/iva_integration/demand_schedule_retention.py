"""Compact old research scheduler diagnostics without touching business state.

Only ``scheduled_runs.result`` is writable here. A caller must enforce the VPS
idle/deployment guards; this module never starts services, sends notifications,
replays work, deletes runs, or vacuums the shared financial database. Returned
savings describe JSON payload bytes, not immediately released filesystem space.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re


FORMAT = 'demand-run-summary-v1'
MAX_BATCH = 500
KEEP_RECENT = 5
PRESERVE = ('status', 'ok', 'has_more', 'cycle_id', 'stage', 'error',
            'error_category', 'text', 'summary')
CYCLE_KEYS = {'cycle_id', 'demand_cycle_id', 'active_cycle_id'}
RUN_KEYS = {'run_id'}


def _moment(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('retention_requires_aware_datetime')
    return value.astimezone(timezone.utc)


def _arguments(cutoff, now, batch_size, protected_cycle_ids):
    cutoff = _moment(cutoff)
    now = _moment(now if now is not None else datetime.now(timezone.utc))
    if cutoff > now:
        raise ValueError('retention_cutoff_is_in_future')
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH:
        raise ValueError('retention_batch_must_be_between_1_and_500')
    if isinstance(protected_cycle_ids, (str, bytes)):
        raise ValueError('protected_cycles_must_be_a_collection')
    cycles = set(protected_cycle_ids)
    if any(not isinstance(value, str) or not value for value in cycles):
        raise ValueError('protected_cycle_id_invalid')
    return cutoff, now, cycles


def _rows(connection, sql, args=()):
    cursor = connection.execute(sql, args)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _object(raw):
    if not isinstance(raw, str):
        return None
    try:
        # Non-finite constants are not JSON and must not become a new summary.
        def reject_constant(_):
            raise ValueError('non_finite')
        def unique_pairs(pairs):
            value = {}
            for key, child in pairs:
                if key in value:
                    raise ValueError('duplicate_json_key')
                value[key] = child
            return value
        value = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
    except (ValueError, TypeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _references(value):
    """Extract only identifiers; never return cursor credentials or full values."""
    cycles, runs = set(), set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in CYCLE_KEYS | RUN_KEYS and child is not None and not isinstance(child, str):
                    raise ValueError('reference_id_invalid')
                if key in CYCLE_KEYS and isinstance(child, str) and child:
                    cycles.add(child)
                if key in RUN_KEYS and isinstance(child, str) and child:
                    runs.add(child)
                if isinstance(child, (dict, list)):
                    pending.append(child)
        elif isinstance(item, list):
            pending.extend(child for child in item if isinstance(child, (dict, list)))
    return cycles, runs


def _compacted(value):
    marker = value.get('storage')
    return (isinstance(marker, dict) and marker.get('format') == FORMAT
            and isinstance(marker.get('compacted_at'), str)
            and type(marker.get('original_bytes')) is int and marker['original_bytes'] >= 0
            and isinstance(marker.get('sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', marker['sha256']) is not None)


def _summary(raw, value, stamp):
    encoded = raw.encode('utf-8')
    kept = {key: value[key] for key in PRESERVE if key in value}
    kept['storage'] = {'format': FORMAT, 'compacted_at': stamp,
                       'original_bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()}
    result = json.dumps(kept, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return result, len(encoded), len(result.encode('utf-8'))


def _analyze(connection, cutoff, now, cycles, batch_size):
    stamp = now.isoformat(timespec='seconds')
    skipped = Counter()
    all_jobs = _rows(connection, 'SELECT id,spec,state,cursor FROM scheduled_jobs ORDER BY id')
    jobs, blocked_jobs, protected_runs = [], set(), set()
    for job in all_jobs:
        spec = _object(job['spec'])
        if not spec or spec.get('kind') != 'demand_research':
            continue
        jobs.append(job)
        cursor = _object(job['cursor'])
        if cursor is None:
            blocked_jobs.add(job['id']); skipped['job_cursor_invalid'] += 1
        else:
            try:
                cursor_cycles, cursor_runs = _references(cursor)
                cycles.update(cursor_cycles); protected_runs.update(cursor_runs)
            except ValueError:
                blocked_jobs.add(job['id']); skipped['job_cursor_invalid'] += 1
        if job['state'] == 'running':
            blocked_jobs.add(job['id']); skipped['job_running'] += 1

    job_ids = {job['id'] for job in jobs}
    for row in _rows(connection, "SELECT job_id,run_id FROM scheduled_outbox WHERE state IN ('pending','sending','uncertain')"):
        if row['job_id'] in job_ids and isinstance(row['run_id'], str):
            protected_runs.add(row['run_id'])
    for job in jobs:
        recent = _rows(connection, 'SELECT id FROM scheduled_runs WHERE job_id=? ORDER BY rowid DESC LIMIT ?',
                       (job['id'], KEEP_RECENT))
        protected_runs.update(row['id'] for row in recent)
        # Keep several complete historical outcomes as well as recent per-stage
        # checkpoints. CASE prevents json_extract from parsing malformed data.
        finals = _rows(connection, '''SELECT id FROM scheduled_runs WHERE job_id=?
            AND finished IS NOT NULL AND state NOT IN ('running','pending')
            AND CASE WHEN json_valid(result) THEN json_type(result,'$.has_more') END='false'
            ORDER BY rowid DESC LIMIT ?''', (job['id'], KEEP_RECENT))
        protected_runs.update(row['id'] for row in finals)

    selected, considered, more = [], 0, False
    for job in jobs:
        if job['id'] in blocked_jobs:
            continue
        cursor = connection.execute('''SELECT id,job_id,state,finished,result FROM scheduled_runs
            WHERE job_id=? AND finished IS NOT NULL AND state NOT IN ('running','pending')
            AND julianday(finished)<julianday(?)
            ORDER BY rowid''', (job['id'], cutoff.isoformat()))
        for identity, job_id, state, finished, raw in cursor:
            considered += 1
            if identity in protected_runs:
                skipped['run_protected'] += 1
                continue
            try:
                if _moment(finished) >= cutoff:
                    skipped['run_not_old'] += 1
                    continue
            except (ValueError, TypeError):
                skipped['finished_invalid'] += 1
                continue
            value = _object(raw)
            if value is None:
                skipped['result_invalid'] += 1
                continue
            if _compacted(value):
                skipped['already_compacted'] += 1
                continue
            if 'storage' in value:
                skipped['unrecognized_storage_marker'] += 1
                continue
            try:
                references, _ = _references(value)
            except ValueError:
                skipped['result_shape_invalid'] += 1
                continue
            if references.intersection(cycles):
                skipped['cycle_protected'] += 1
                continue
            # Preserve malformed legacy result shapes verbatim; do not infer
            # whether an ambiguous result represented a success or continuation.
            if (('ok' in value and type(value['ok']) is not bool)
                    or ('has_more' in value and type(value['has_more']) is not bool)
                    or ('cycle_id' in value and value['cycle_id'] is not None and not isinstance(value['cycle_id'], str))):
                skipped['result_shape_invalid'] += 1
                continue
            try:
                replacement, before, after = _summary(raw, value, stamp)
            except (ValueError, TypeError, RecursionError, UnicodeError):
                skipped['result_invalid'] += 1
                continue
            if after >= before:
                skipped['no_savings'] += 1
                continue
            if len(selected) == batch_size:
                more = True
                break
            selected.append({'id': identity, 'job_id': job_id, 'state': state, 'finished': finished,
                             'raw': raw, 'replacement': replacement, 'before': before, 'after': after})
        cursor.close()
        if more:
            break

    public = {'format': FORMAT, 'cutoff': cutoff.isoformat(timespec='seconds'),
              'at': stamp, 'batch_size': batch_size, 'jobs_considered': len(jobs),
              'protected_job_ids': sorted(blocked_jobs), 'protected_cycle_ids': sorted(cycles),
              'protected_run_count': len(protected_runs), 'considered_rows': considered,
              'candidate_rows': len(selected), 'candidate_run_ids': [row['id'] for row in selected],
              'original_bytes': sum(row['before'] for row in selected),
              'compacted_bytes': sum(row['after'] for row in selected),
              'estimated_savings_bytes': sum(row['before'] - row['after'] for row in selected),
              'more_eligible': more, 'skipped': dict(sorted(skipped.items()))}
    return public, selected


@contextmanager
def _read_snapshot(connection):
    own = not connection.in_transaction
    if own:
        connection.execute('BEGIN')
    try:
        yield
    finally:
        if own:
            connection.rollback()


def plan(connection, cutoff, *, protected_cycle_ids=(), batch_size=MAX_BATCH, now=None):
    """Read-only consistent dry run; no result text or credentials are returned."""
    cutoff, now, cycles = _arguments(cutoff, now, batch_size, protected_cycle_ids)
    with _read_snapshot(connection):
        public, _ = _analyze(connection, cutoff, now, cycles, batch_size)
    return {**public, 'dry_run': True, 'updated_rows': 0}


def apply(connection, cutoff, *, protected_cycle_ids=(), batch_size=MAX_BATCH, now=None):
    """Recheck all guards and compact one batch atomically; caller controls idle."""
    cutoff, now, cycles = _arguments(cutoff, now, batch_size, protected_cycle_ids)
    if connection.in_transaction:
        raise ValueError('retention_apply_requires_own_transaction')
    previous_secure_delete = connection.execute('PRAGMA secure_delete').fetchone()[0]
    connection.execute('BEGIN IMMEDIATE')
    try:
        # SQLite backups copy allocated/free pages. Clear replaced payload from
        # those pages, otherwise diagnostics would survive in later snapshots.
        connection.execute('PRAGMA secure_delete=ON')
        public, selected = _analyze(connection, cutoff, now, cycles, batch_size)
        for row in selected:
            changed = connection.execute('''UPDATE scheduled_runs SET result=?
                WHERE id=? AND job_id=? AND state=? AND finished=? AND result=?''',
                (row['replacement'], row['id'], row['job_id'], row['state'], row['finished'], row['raw'])).rowcount
            if changed != 1:
                raise RuntimeError('research_run_changed_during_retention')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        mode = 'FAST' if previous_secure_delete == 2 else ('ON' if previous_secure_delete else 'OFF')
        connection.execute('PRAGMA secure_delete=' + mode)
    return {**public, 'dry_run': False, 'updated_rows': len(selected),
            'reclaimed_logical_bytes': public['estimated_savings_bytes']}
