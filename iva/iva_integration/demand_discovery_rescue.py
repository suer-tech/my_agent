"""One persisted alternative-source search when a role read no source bodies.

The caller owns the normal research transaction and resource lease. This module
does not fetch, invoke a model, change routes, or retry access to closed sites.
"""
import json
from urllib.parse import urlsplit


EVENT = 'discovery_rescue_scheduled'
CLOSED_ERRORS = frozenset(('access_denied', 'access_challenge', 'authentication_required',
                           'login_required', 'rate_limited'))


def host(url):
    return (urlsplit(url).hostname or '').casefold()


def context(store, cycle_id, batch_index):
    for row in store.rows("SELECT detail FROM demand_events WHERE cycle_id=? AND stage='agents' AND event=? ORDER BY id",
                          (cycle_id, EVENT)):
        value = json.loads(row['detail'])
        if value.get('batch_index') == batch_index:
            return value
    return None


def task_url_count(store, cycle_id, task_id):
    return store.db.execute('''SELECT count(*) FROM demand_candidates c JOIN demand_batches b
        ON b.cycle_id=c.cycle_id AND b.batch_index=c.batch_index
        WHERE c.cycle_id=? AND b.agent_task_id=?''', (cycle_id, task_id)).fetchone()[0]


def schedule(store, cycle, task, root_limit, child_limit, default_cycle_limit):
    """Append one retry batch to the SAME role without rewriting its history."""
    plan = json.loads(cycle['plan']) if isinstance(cycle['plan'], str) else cycle['plan']
    current = store.rows('SELECT state FROM demand_cycles WHERE id=?', (cycle['id'],))
    if (not current or current[0]['state'] != 'running' or plan.get('version', 0) < 3
            or task['status'] != 'running'):
        return False
    for row in store.rows("SELECT event,detail FROM demand_events WHERE cycle_id=? AND stage='agents' AND event IN (?, 'role_report')",
                          (cycle['id'], EVENT)):
        if json.loads(row['detail']).get('task_id') == task['id']:
            return False
    batches = store.rows('SELECT * FROM demand_batches WHERE cycle_id=? AND agent_task_id=? ORDER BY batch_index',
                         (cycle['id'], task['id']))
    # A technical discovery failure is handled by the normal bounded stage
    # retry. Only a committed, completed discovery may request new sources.
    if (len(batches) != 1 or batches[0]['state'] != 'done' or not batches[0]['discovery']
            or batches[0]['error'] not in (None, 'no_new_candidates')):
        return False
    rows = store.rows('SELECT url,state,error FROM demand_candidates WHERE cycle_id=? AND batch_index=?',
                      (cycle['id'], batches[0]['batch_index']))
    if any(row['state'] != 'failed' for row in rows):
        return False
    count = store.db.execute('SELECT count(*) FROM demand_candidates WHERE cycle_id=?', (cycle['id'],)).fetchone()[0]
    task_limit = child_limit if task['parent_task_id'] else root_limit
    remaining = min(task_limit - len(rows), plan.get('max_urls', default_cycle_limit) - count)
    if remaining <= 0:
        return False
    index = store.db.execute('SELECT COALESCE(MAX(batch_index),-1)+1 FROM demand_batches WHERE cycle_id=?',
                             (cycle['id'],)).fetchone()[0]
    detail = {'task_id': task['id'], 'batch_index': index, 'original_batch_index': batches[0]['batch_index'],
              'attempt': 1, 'reason': 'no_readable_role_sources', 'remaining_urls': remaining,
              'failed_urls': sorted(row['url'] for row in rows),
              'closed_hosts': sorted({host(row['url']) for row in rows if row['error'] in CLOSED_ERRORS}),
              'previous_queries': json.loads(batches[0]['discovery']).get('queries', [])[:4]}
    store.db.execute('''INSERT INTO demand_batches(cycle_id,batch_index,focus,state,agent_task_id)
        VALUES(?,?,?,'pending',?)''',
        (cycle['id'], index, batches[0]['focus'] + ' Дополнительный поиск доступных источников после пустого корпуса.', task['id']))
    store.event(cycle['id'], 'agents', EVENT, detail)
    return True
