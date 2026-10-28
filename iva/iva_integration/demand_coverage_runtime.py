"""Persisted same-assignment evidence repair, under the caller's transaction.

This module never invokes a model, starts a job or reads a network resource.
Missing evidence is a collection deficit, not a quote-grounded child proposal.
"""
import hashlib
import json

from iva_integration import demand_discovery_rescue as rescue
from iva_integration import demand_agents as agents

VERSION = 1
MAX_SEARCHES = 2
ASSESSED = 'evidence_coverage_assessed'
SEARCH = 'evidence_coverage_search'
CLOSED = 'evidence_coverage_closed'


def plan(cycle):
    value = cycle.get('plan') or {}
    return json.loads(value) if isinstance(value, str) else value


def enabled(cycle):
    return plan(cycle).get('evidence_coverage_version') == VERSION


def events(store, cycle_id, task_id, event):
    values = [json.loads(row['detail']) for row in store.rows(
        "SELECT detail FROM demand_events WHERE cycle_id=? AND stage='agents' AND event=? ORDER BY id",
        (cycle_id, event))]
    return [item for item in values if item.get('task_id') == task_id]


def closed(store, cycle_id, task_id):
    rows = events(store, cycle_id, task_id, CLOSED)
    return rows[-1] if rows else None


def context(store, cycle_id, batch_index):
    for row in store.rows("SELECT detail FROM demand_events WHERE cycle_id=? AND stage='agents' AND event=? ORDER BY id", (cycle_id, SEARCH)):
        value = json.loads(row['detail'])
        if value['batch_index'] == batch_index:
            return value
    return None


def candidates(store, cycle_id, task_id):
    return store.rows('''SELECT c.url,c.state,c.error,c.source_id,c.analysis_state,b.batch_index
        FROM demand_candidates c JOIN demand_batches b ON b.cycle_id=c.cycle_id AND b.batch_index=c.batch_index
        WHERE c.cycle_id=? AND b.agent_task_id=? ORDER BY c.url''', (cycle_id, task_id))


def signature(store, cycle_id, task_id):
    batches = store.rows('''SELECT batch_index,state,error FROM demand_batches
        WHERE cycle_id=? AND agent_task_id=? ORDER BY batch_index''', (cycle_id, task_id))
    data = [candidates(store, cycle_id, task_id), batches]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def assessment(store, cycle_id, task_id):
    stamp = signature(store, cycle_id, task_id)
    rows = events(store, cycle_id, task_id, ASSESSED)
    return next((item['assessment'] for item in reversed(rows) if item['signature'] == stamp), None)


def close(store, cycle, task, value, reason, status='deferred'):
    existing = closed(store, cycle['id'], task['id'])
    if existing:
        return existing
    searches = events(store, cycle['id'], task['id'], SEARCH)
    detail = {'version': VERSION, 'task_id': task['id'], 'role': task['role'],
              'question': task['question'], 'expected_evidence': task['expected_evidence'],
              'status': status, 'stop_reason': reason, 'assessment': value,
              'attempts': len(searches) + len(events(store, cycle['id'], task['id'], rescue.EVENT)),
              'searches': [{'batch_index': item['batch_index'], 'queries': item['queries'],
                            'missing': item['missing']} for item in searches],
              'continuation': 'Saved for same-scope planning; no automatic job or unlimited retry.'}
    store.event(cycle['id'], 'agents', CLOSED, detail)
    return detail


def settle(store, cycle, task, value, root_limit=12, child_limit=6, cycle_limit=72):
    """Atomically close the assessment or append one bounded collection batch."""
    if not enabled(cycle) or cycle['state'] != 'running' or task['status'] != 'running':
        return False
    if closed(store, cycle['id'], task['id']):
        return False
    batches = store.rows('SELECT * FROM demand_batches WHERE cycle_id=? AND agent_task_id=? ORDER BY batch_index', (cycle['id'], task['id']))
    rows = candidates(store, cycle['id'], task['id'])
    if (not batches or any(b['state'] not in ('done', 'failed') for b in batches)
            or any(r['state'] == 'pending' or r['state'] == 'read' and r['analysis_state'] == 'pending' for r in rows)):
        raise ValueError('coverage_frontier_not_terminal')
    previous = events(store, cycle['id'], task['id'], ASSESSED)
    stamp = signature(store, cycle['id'], task['id'])
    if not any(item['signature'] == stamp for item in previous):
        store.event(cycle['id'], 'agents', ASSESSED, {'task_id': task['id'], 'signature': stamp, 'assessment': value})
    searches = events(store, cycle['id'], task['id'], SEARCH)
    attempts = len(searches) + len(events(store, cycle['id'], task['id'], rescue.EVENT))
    total = store.db.execute('SELECT count(*) FROM demand_candidates WHERE cycle_id=?', (cycle['id'],)).fetchone()[0]
    remaining = min((child_limit if task['parent_task_id'] else root_limit) - len(rows),
                    plan(cycle).get('max_urls', cycle_limit) - total)
    technical = any(b['state'] == 'failed' for b in batches) or any(r['analysis_state'] == 'failed' and r['state'] == 'read' for r in rows)
    if technical:
        reason = 'collection_technical_failure'
    elif value['sufficient']:
        close(store, cycle, task, value, 'requirements_met', 'sufficient')
        return False
    elif remaining <= 0:
        reason = 'url_budget_exhausted'
    elif attempts >= MAX_SEARCHES:
        reason = 'search_rounds_exhausted'
    elif searches and batches[-1]['error'] == 'no_new_candidates':
        reason = 'no_new_sources'
    else:
        reason = None
    if reason:
        close(store, cycle, task, value, reason)
        return False
    index = store.db.execute('SELECT COALESCE(MAX(batch_index),-1)+1 FROM demand_batches WHERE cycle_id=?', (cycle['id'],)).fetchone()[0]
    allowance = max(1, (remaining + (MAX_SEARCHES - attempts) - 1) // (MAX_SEARCHES - attempts))
    detail = {'task_id': task['id'], 'batch_index': index, 'attempt': attempts + 1,
              'remaining_urls': remaining, 'candidate_limit': allowance,
              'reason': 'missing_assignment_evidence', 'missing': value['missing'],
              'queries': value['queries'], 'failed_urls': [r['url'] for r in rows if r['state'] == 'failed'],
              'closed_hosts': sorted({rescue.host(r['url']) for r in rows if r['error'] in rescue.CLOSED_ERRORS}),
              'previous_queries': [q for b in batches if b['discovery'] for q in json.loads(b['discovery']).get('queries', [])]}
    store.db.execute('''INSERT INTO demand_batches(cycle_id,batch_index,focus,state,agent_task_id)
        VALUES(?,?,?,'pending',?)''', (cycle['id'], index, task['role'] + ': ' + task['question'] +
        '. Адресное восполнение недостающих свидетельств в том же поручении.', task['id']))
    store.event(cycle['id'], 'agents', SEARCH, detail)
    return True


def summary(store, cycle, tasks):
    if not enabled(cycle):
        return None
    result = []
    for task in tasks:
        saved = (task.get('result') or {}).get('evidence_coverage') or closed(store, cycle['id'], task['id'])
        result.append({'task_id': task['id'], 'role': task['role'],
                       'status': saved['status'] if saved else 'not_assessed',
                       'stop_reason': saved.get('stop_reason') if saved else task.get('status'),
                       'question': task['question'],
                       'missing': (saved.get('assessment') or {}).get('missing', []) if saved else [],
                       'attempts': saved.get('attempts', 0) if saved else 0})
    return {'version': VERSION, 'tasks': result, 'complete': bool(result) and all(r['status'] == 'sufficient' for r in result),
            'meaning': 'Collection adequacy is an engineering check, not market validation.'}


def prior_gaps(store, cycle):
    """Only the identical scope can reuse unfinished questions; no lane mixing."""
    boundary = (plan(cycle).get('scope') or {}).get('id')
    if not boundary:
        return []
    rows = store.rows("SELECT id,plan FROM demand_cycles WHERE job_id=? AND id<>? AND state IN ('completed','completed_with_gaps') ORDER BY sequence DESC LIMIT 24", (cycle['job_id'], cycle['id']))
    for row in rows:
        if (json.loads(row['plan']).get('scope') or {}).get('id') != boundary:
            continue
        tasks = agents.snapshot(store.db, row['id'])['tasks']
        values = []
        for task in tasks:
            closed_value = (task.get('result') or {}).get('evidence_coverage')
            if closed_value and closed_value['status'] != 'sufficient':
                values.append({'cycle_id': row['id'], 'task_id': task['id'], 'question': closed_value['question'],
                               'expected_evidence': closed_value['expected_evidence'],
                               'missing': (closed_value.get('assessment') or {}).get('missing', []),
                               'stop_reason': closed_value['stop_reason']})
        return values[:8]
    return []
