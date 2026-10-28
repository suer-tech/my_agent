"""Bound old research trace, retaining conclusions and source provenance.

This module owns only the research database. The deployment runner owns backup,
runtime exclusion, scheduler protections and optional SQLite space reclamation.
No cards, vault exports or financial rows are changed here.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json


VERSION = 1
TERMINAL = frozenset(('completed', 'completed_with_gaps', 'failed', 'cancelled'))
TRACE_TABLES = ('demand_events', 'demand_agent_events', 'demand_agent_plans',
                'demand_agent_tasks', 'demand_agent_syntheses')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS demand_cycle_archives(
        cycle_id TEXT PRIMARY KEY,version INTEGER NOT NULL,compacted_at TEXT NOT NULL,
        retention_days INTEGER NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS demand_source_retention(
        source_id TEXT PRIMARY KEY,compacted_at TEXT NOT NULL,original_bytes INTEGER NOT NULL,
        original_sha256 TEXT NOT NULL,reason TEXT NOT NULL)''')


def archived(db, cycle_id):
    """Read the frozen semantic record; an invalid archive must fail closed."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demand_cycle_archives'").fetchone():
        return None
    row = db.execute('SELECT version,compacted_at,payload,sha256 FROM demand_cycle_archives WHERE cycle_id=?', (cycle_id,)).fetchone()
    if not row:
        return None
    if row[0] != VERSION or _hash(row[2]) != row[3]:
        raise ValueError('demand_retention_archive_invalid')
    value = json.loads(row[2])
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ValueError('demand_retention_archive_invalid')
    return {**value, 'compacted_at': row[1]}


def restore_source(db, source_id):
    """Called only after a real successful fetch has restored the source body."""
    db.execute('DELETE FROM demand_source_retention WHERE source_id=?', (source_id,))


def _bytes(values):
    return sum(len(str(value).encode('utf-8')) for value in values if value is not None)


def logical_bytes(db):
    total = 0
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'demand_%'"):
        if not table.replace('_', '').isalnum():
            raise ValueError('unsafe_research_table')
        columns = [row[1] for row in db.execute('PRAGMA table_info("' + table + '")')]
        expression = '+'.join('COALESCE(length(CAST("' + name + '" AS BLOB)),0)' for name in columns)
        total += db.execute('SELECT COALESCE(sum(' + expression + '),0) FROM "' + table + '"').fetchone()[0]
    return total


def _refs(value, identities):
    if isinstance(value, str):
        return {value} if value in identities else set()
    if isinstance(value, list):
        return set().union(*(_refs(item, identities) for item in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(_refs(item, identities) for item in value.values())) if value else set()
    return set()


def _parsed(value):
    return json.loads(value) if value else None


def _older(value, cutoff):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.tzinfo is not None and parsed < cutoff
    except (TypeError, ValueError, AttributeError):
        return False  # Unknown age must never authorize deletion.


def _slim_discovery(value):
    decoded = _parsed(value)
    if not isinstance(decoded, dict):
        return value
    # Search result lists are operational input; actual read source provenance
    # remains in candidates/sources and the verified observations archive.
    decoded.pop('web_events', None)
    return _json(decoded)


def _build_archive(store, cycle, stamp):
    from iva_integration import demand_agents as agents
    from iva_integration import demand_research as research
    view = research._reported_cycle(store, cycle)
    coverage = dict(view['coverage'])
    coverage.pop('orchestration', None)
    result = dict(view.get('result') or {})
    result.pop('coverage', None)
    plan = dict(view['plan'])
    plan.pop('baseline', None)
    plan.pop('prior_context', None)
    events = store.rows('SELECT id,stage,event,detail,created FROM demand_events WHERE cycle_id=? ORDER BY id', (cycle['id'],))
    semantic = []
    for event in events:
        detail = _parsed(event['detail'])
        if event['event'] == 'chunk_completed':
            detail = dict(detail)
            detail.pop('agent_observations', None)  # Kept once in observations.
        elif event['stage'] != 'question' and event['event'] not in ('failed', 'exhausted'):
            continue
        semantic.append({**event, 'detail': detail})
    agent_cycle = research._agent_cycle(cycle)
    return {'version': VERSION, 'coverage': coverage, 'plan': plan, 'result': result,
            'recorded_state': cycle['state'], 'reported_state': view['state'],
            'snapshot': agents.snapshot(store.db, cycle['id']) if agent_cycle else None,
            'observations': research._agent_observations(store, cycle['id']),
            'semantic_events': semantic,
            'agent_audit': [{**event, 'detail': _parsed(event['detail'])} for event in store.rows(
                'SELECT id,task_id,event,detail,created FROM demand_agent_events WHERE cycle_id=? ORDER BY id', (cycle['id'],))],
            'event_counts': dict(Counter(event['stage'] + '/' + event['event'] for event in events)),
            'coverage_view': 'preserved_at_compaction',
            'source_provenance': 'demand_candidates and demand_sources metadata retained; selected old bodies removed',
            'compacted_at': stamp}


def _plan(store, retention_days, clock, protected_cycle_ids, max_cycles, max_source_bodies):
    stamp = clock.isoformat(timespec='seconds')
    cutoff_clock = clock - timedelta(days=retention_days)
    cutoff = cutoff_clock.isoformat(timespec='seconds')
    protected = set(protected_cycle_ids)
    cycles = store.rows('SELECT id,stage,state,finished FROM demand_cycles ORDER BY created,id')
    existing = {row['cycle_id'] for row in store.rows('SELECT cycle_id FROM demand_cycle_archives')}
    compact = []
    delta = 0
    eligible_cycles = 0
    for cycle in cycles:
        identity = cycle['id']
        if identity in existing or identity in protected:
            continue
        if cycle['stage'] != 'done' or cycle['state'] not in TERMINAL or not _older(cycle['finished'], cutoff_clock):
            continue
        if store.rows("SELECT 1 FROM demand_candidates WHERE cycle_id=? AND (state IN ('pending','running') OR (state='read' AND analysis_state IN ('pending','running'))) LIMIT 1", (identity,)):
            continue
        if store.rows("SELECT 1 FROM demand_batches WHERE cycle_id=? AND state IN ('pending','running') LIMIT 1", (identity,)):
            continue
        if store.rows("SELECT 1 FROM demand_agent_tasks WHERE cycle_id=? AND status IN ('pending','running') LIMIT 1", (identity,)):
            continue
        if store.rows("SELECT 1 FROM demand_questions WHERE active_cycle_id=? AND state='running' LIMIT 1", (identity,)):
            continue
        eligible_cycles += 1
        if len(compact) >= max_cycles:
            continue
        cycle = store.rows('SELECT * FROM demand_cycles WHERE id=?', (identity,))[0]
        payload = _build_archive(store, cycle, stamp)
        encoded = _json(payload)
        # Hash verification takes place before any rows are removed.
        decoded = json.loads(encoded)
        if decoded != payload:
            raise ValueError('demand_retention_archive_roundtrip')
        plan = _json(payload['plan'])
        result = _json(payload['result'])
        discovery = _slim_discovery(cycle['discovery'])
        batches = []
        for row in store.rows('SELECT batch_index,discovery FROM demand_batches WHERE cycle_id=?', (identity,)):
            replacement = _slim_discovery(row['discovery'])
            delta += _bytes((replacement,)) - _bytes((row['discovery'],))
            batches.append((replacement, identity, row['batch_index']))
        for table in TRACE_TABLES:
            for row in store.db.execute('SELECT * FROM ' + table + ' WHERE cycle_id=?', (identity,)):
                delta -= _bytes(row)
        delta += _bytes((identity, VERSION, stamp, retention_days, encoded, _hash(encoded)))
        delta += _bytes((plan, result, discovery)) - _bytes((cycle['plan'], cycle['result'], cycle['discovery']))
        compact.append({'id': identity, 'archive': encoded, 'payload': payload, 'plan': plan,
                        'result': result, 'discovery': discovery, 'batches': batches})
    archived_ids = existing | {item['id'] for item in compact}
    sources = store.rows('SELECT id,length(CAST(text AS BLOB)) body_bytes,fetched_at FROM demand_sources ORDER BY fetched_at,id')
    source_ids = {row['id'] for row in sources}
    pins = {row['source_id'] for row in store.rows('SELECT DISTINCT source_id FROM demand_evidence')}
    # Any non-compacted cycle still owns its complete input and restart trace.
    for row in store.rows('SELECT cycle_id,source_id FROM demand_candidates WHERE source_id IS NOT NULL'):
        if row['cycle_id'] not in archived_ids or row['cycle_id'] in protected:
            pins.add(row['source_id'])
    for table, columns in (('demand_hypotheses', ('payload',)),
                           ('demand_revisions', ('payload',)),
                           ('demand_questions', ('payload', 'result'))):
        for row in store.rows('SELECT ' + ','.join(columns) + ' FROM ' + table):
            for column in columns:
                pins.update(_refs(_parsed(row[column]), source_ids))
    for row in store.rows("SELECT payload,result,source_ids FROM demand_agent_tasks WHERE status IN ('deferred','failed','pending','running')"):
        for value in row.values():
            pins.update(_refs(_parsed(value), source_ids))
    # Failed/deferred assignments remain actionable after their trace is compacted.
    def archive_values():
        yield from (item['payload'] for item in compact)
        for identity in existing:
            yield archived(store.db, identity)
    for value in archive_values():
        for task in (value.get('snapshot') or {}).get('tasks', []):
            if task['status'] in ('failed', 'deferred', 'pending', 'running'):
                pins.update(_refs(task, source_ids))
    prune = []
    eligible_sources = 0
    for source in sources:
        if source['id'] in pins or not source['body_bytes'] or not _older(source['fetched_at'], cutoff_clock):
            continue
        eligible_sources += 1
        if len(prune) >= max_source_bodies:
            continue
        body = store.db.execute('SELECT text FROM demand_sources WHERE id=?', (source['id'],)).fetchone()[0]
        record = (source['id'], stamp, source['body_bytes'], _hash(body), 'old_unreferenced_body')
        previous = store.db.execute('SELECT * FROM demand_source_retention WHERE source_id=?', (source['id'],)).fetchone()
        delta += _bytes(record) - (_bytes(previous) if previous else 0) - record[2]
        prune.append(record)
    return compact, prune, delta, cutoff, sorted(protected), len(pins), eligible_cycles, eligible_sources


def compact(store, *, retention_days=14, clock=None, protected_cycle_ids=(), dry_run=True,
            max_cycles=48, max_source_bodies=1000):
    """Return counts/UTF-8 logical bytes; apply all changes atomically if requested.

    Dry-run performs no writes. Caller must pass scheduler-owned cycle IDs and
    hold the runtime maintenance guard before apply. Schema is initialized by
    Research, and a failed plan/JSON decode leaves all original records intact.
    """
    if type(retention_days) is not int or retention_days < 1:
        raise ValueError('invalid_retention_days')
    if type(max_cycles) is not int or not 1 <= max_cycles <= 1000 or type(max_source_bodies) is not int or not 1 <= max_source_bodies <= 10000:
        raise ValueError('invalid_retention_batch_limits')
    clock = clock or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError('retention_clock_must_be_aware')
    clock = clock.astimezone(timezone.utc)
    if store.db.in_transaction:
        raise ValueError('retention_requires_idle_connection')
    if not dry_run:
        store.db.execute('PRAGMA secure_delete=ON')
    store.db.execute('BEGIN' if dry_run else 'BEGIN IMMEDIATE')
    try:
        before = logical_bytes(store.db)
        records, prune, delta, cutoff, protected, pinned, eligible_cycles, eligible_sources = _plan(
            store, retention_days, clock, protected_cycle_ids, max_cycles, max_source_bodies)
        after = before + delta
        if not dry_run:
            stamp = clock.isoformat(timespec='seconds')
            for record in records:
                identity = record['id']
                store.db.execute('INSERT INTO demand_cycle_archives VALUES(?,?,?,?,?,?)',
                                 (identity, VERSION, stamp, retention_days, record['archive'], _hash(record['archive'])))
                if archived(store.db, identity) is None:
                    raise ValueError('demand_retention_archive_missing')
                store.db.execute('UPDATE demand_cycles SET plan=?,result=?,discovery=? WHERE id=?',
                                 (record['plan'], record['result'], record['discovery'], identity))
                store.db.executemany('UPDATE demand_batches SET discovery=? WHERE cycle_id=? AND batch_index=?', record['batches'])
                for table in TRACE_TABLES:
                    store.db.execute('DELETE FROM ' + table + ' WHERE cycle_id=?', (identity,))
            for record in prune:
                store.db.execute('INSERT OR REPLACE INTO demand_source_retention VALUES(?,?,?,?,?)', record)
                store.db.execute("UPDATE demand_sources SET text='' WHERE id=?", (record[0],))
            after = logical_bytes(store.db)
            if after != before + delta:
                raise ValueError('demand_retention_size_mismatch')
            store.db.commit()
        else:
            store.db.rollback()
        return {'ok': True, 'dry_run': dry_run, 'retention_days': retention_days, 'cutoff': cutoff,
                'cycles_compacted': len(records), 'cycle_ids': [item['id'] for item in records],
                'source_bodies_pruned': len(prune), 'source_bytes_released': sum(row[2] for row in prune),
                'logical_bytes_before': before, 'logical_bytes_after': after,
                'logical_bytes_reclaimed': before - after, 'protected_cycles': protected,
                'pinned_sources': pinned, 'eligible_cycles': eligible_cycles, 'eligible_source_bodies': eligible_sources,
                'more_eligible': eligible_cycles > len(records) or eligible_sources > len(prune),
                'max_cycles': max_cycles, 'max_source_bodies': max_source_bodies,
                'physical_reclamation': 'SQLite pages are reusable; VACUUM is runner-owned'}
    except BaseException:
        store.db.rollback()
        raise
