"""Durable sequential research roles and coordinator admissions, without I/O.

All writes belong to the caller's SQLite transaction. Plans describe intended
tests; task results describe observations. Neither role count nor completion
establishes demand: the core owns quote, evidence and hypothesis validation.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

ROLES = ('buyer_problem', 'alternatives', 'foreign', 'critic')
MAX_TASKS = 8
MAX_CHILDREN = 4
MAX_CHILDREN_PER_PARENT = 2
MAX_DEPTH = 2
MAX_SYNTHESIS_ROUNDS = 2


def _text(minimum=20, maximum=2000):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


TASK_SCHEMA = _object({
    'key': _text(3, 100), 'role': {'type': 'string', 'enum': list(ROLES)},
    'question': _text(), 'scope': _text(), 'methods': _text(),
    'queries': {'type': 'array', 'items': _text(8, 500), 'minItems': 2, 'maxItems': 4},
    'support_condition': _text(), 'refute_condition': _text(),
    'expected_evidence': _text(), 'deliverable': _text(),
})
PLAN_SCHEMA = _object({
    'objective': _text(30), 'decision': _text(30), 'rationale': _text(30, 4000),
    'researchers': {'type': 'array', 'items': TASK_SCHEMA, 'minItems': 4, 'maxItems': 4},
})
CHILD_SCHEMA = _object({**TASK_SCHEMA['properties'],
    'source_ids': {'type': 'array', 'items': _text(1, 100), 'minItems': 1, 'maxItems': 6},
    'trigger_quote': _text(20, 500), 'why_needed': _text(30, 2000),
})


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _norm(value):
    return ' '.join(re.sub(r'[^\w]+', ' ', value.casefold()).split())


def _literal(value):
    return ' '.join(value.casefold().split())


def _rows(db, query, args=()):
    cursor = db.execute(query, args)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['required']):
            raise ValueError('agent_shape_invalid')
        for key, child in schema['properties'].items():
            _validate(value[key], child)
    elif kind == 'array':
        if not isinstance(value, list) or not schema['minItems'] <= len(value) <= schema['maxItems']:
            raise ValueError('agent_array_invalid')
        for child in value:
            _validate(child, schema['items'])
    elif (not isinstance(value, str) or '\x00' in value or
          not schema.get('minLength', 1) <= len(value.strip()) <= schema.get('maxLength', 2000) or
          ('enum' in schema and value not in schema['enum'])):
        raise ValueError('agent_text_invalid')


def _substantive(task):
    for name in ('question', 'scope', 'methods', 'support_condition', 'refute_condition',
                 'expected_evidence', 'deliverable'):
        if len(set(_norm(task[name]).split())) < 4:
            raise ValueError('agent_brief_not_substantive')
    if _norm(task['support_condition']) == _norm(task['refute_condition']):
        raise ValueError('agent_criteria_not_distinct')
    if len({_norm(query) for query in task['queries']}) != len(task['queries']):
        raise ValueError('agent_queries_not_distinct')


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS demand_agent_plans(
        cycle_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,track TEXT NOT NULL,
        payload TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS demand_agent_tasks(
        id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,parent_task_id TEXT,depth INTEGER NOT NULL,
        role TEXT NOT NULL,stable_key TEXT NOT NULL,question_key TEXT NOT NULL,
        ordinal INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,
        result TEXT,source_ids TEXT NOT NULL,reason TEXT,created TEXT NOT NULL,updated TEXT NOT NULL,
        UNIQUE(cycle_id,stable_key),UNIQUE(cycle_id,question_key))''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS demand_agent_one_running
        ON demand_agent_tasks(cycle_id) WHERE status='running' ''')
    db.execute('''CREATE TABLE IF NOT EXISTS demand_agent_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,cycle_id TEXT NOT NULL,task_id TEXT,
        event TEXT NOT NULL,detail TEXT NOT NULL,created TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS demand_agent_syntheses(
        cycle_id TEXT NOT NULL,round INTEGER NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL,
        created TEXT NOT NULL,PRIMARY KEY(cycle_id,round))''')


ensure_schema = ensure


def _event(db, cycle_id, task_id, event, detail):
    db.execute('INSERT INTO demand_agent_events(cycle_id,task_id,event,detail,created) VALUES(?,?,?,?,?)',
               (cycle_id, task_id, event, _json(detail), _now()))


def _decoded(row):
    return {**json.loads(row['payload']), **row, 'payload': json.loads(row['payload']),
            'result': json.loads(row['result']) if row['result'] else None,
            'source_ids': json.loads(row['source_ids']),
            'review_needed': row['status'] == 'deferred'}


def task(db, task_id):
    found = _rows(db, 'SELECT * FROM demand_agent_tasks WHERE id=?', (task_id,))
    if not found:
        raise ValueError('agent_task_not_found')
    return _decoded(found[0])


def plan(db, cycle_id):
    found = _rows(db, 'SELECT * FROM demand_agent_plans WHERE cycle_id=?', (cycle_id,))
    if not found:
        return None
    return {**found[0], **json.loads(found[0]['payload'])}


def _stable_task_key(payload, parent=None):
    # Model keys name proposals within one review; a child may legitimately
    # reuse its parent's key for a different question. Keep historical root
    # keys unchanged, but scope new child keys to parent + actual question.
    # Global question_key below still deduplicates the same question across
    # roles/parents and against children written by earlier releases.
    value = (payload['role'] + ':' + _norm(payload['key']) if parent is None else
             _json(['child-v2', parent['id'], payload['role'], _norm(payload['key']),
                    _norm(payload['question'])]))
    return hashlib.sha256(value.encode()).hexdigest()


def _insert_task(db, cycle_id, payload, ordinal, parent=None, status='pending', reason=None):
    identity = 'agt_' + uuid.uuid4().hex
    stable = _stable_task_key(payload, parent)
    question_key = hashlib.sha256(_norm(payload['question']).encode()).hexdigest()
    db.execute('''INSERT INTO demand_agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               (identity, cycle_id, parent['id'] if parent else None, parent['depth'] + 1 if parent else 0,
                payload['role'], stable, question_key, ordinal, status, _json(payload), None,
                _json(payload.get('source_ids', [])), reason, _now(), _now()))
    return identity


def save_plan(db, cycle_id, job_id, track, value):
    ensure(db)
    _validate(value, PLAN_SCHEMA)
    if any(len(set(_norm(value[field]).split())) < 6 for field in ('objective', 'decision', 'rationale')):
        raise ValueError('agent_plan_not_substantive')
    researchers = value['researchers']
    if {item['role'] for item in researchers} != set(ROLES):
        raise ValueError('agent_roles_incomplete')
    for item in researchers:
        _substantive(item)
    for field in ('key', 'question', 'scope'):
        if len({_norm(item[field]) for item in researchers}) != 4:
            raise ValueError('agent_assignments_not_distinct')
    cycles = _rows(db, 'SELECT job_id,track FROM demand_cycles WHERE id=?', (cycle_id,))
    if not cycles or cycles[0] != {'job_id': job_id, 'track': track}:
        raise ValueError('agent_cycle_scope_mismatch')
    existing = plan(db, cycle_id)
    if existing:
        if existing['payload'] != _json(value) or existing['job_id'] != job_id or existing['track'] != track:
            raise ValueError('agent_plan_is_immutable')
        return snapshot(db, cycle_id)
    # Validate everything before the first insertion. Parent supplies transaction.
    db.execute('INSERT INTO demand_agent_plans VALUES(?,?,?,?,?,?)',
               (cycle_id, job_id, track, _json(value), 'planned', _now()))
    for ordinal, payload in enumerate(researchers):
        _insert_task(db, cycle_id, payload, ordinal)
    _event(db, cycle_id, None, 'plan_validated', {'root_roles': list(ROLES)})
    return snapshot(db, cycle_id)


def next_task(db, cycle_id):
    rows = _rows(db, '''SELECT * FROM demand_agent_tasks WHERE cycle_id=? AND status IN ('pending','running')
        ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,
                 CASE role WHEN 'critic' THEN 1 ELSE 0 END,ordinal,id''', (cycle_id,))
    return _decoded(rows[0]) if rows else None


def start_task(db, task_id):
    row = task(db, task_id)
    if row['status'] == 'running':
        return row
    if row['status'] != 'pending' or next_task(db, row['cycle_id'])['id'] != task_id:
        raise ValueError('agent_task_not_next')
    db.execute("UPDATE demand_agent_tasks SET status='running',updated=? WHERE id=? AND status='pending'",
               (_now(), task_id))
    db.execute("UPDATE demand_agent_plans SET status='running' WHERE cycle_id=?", (row['cycle_id'],))
    _event(db, row['cycle_id'], task_id, 'started', {})
    return task(db, task_id)


def _read_sources(db, cycle_id, allowed):
    allowed = set(allowed)
    return {row['id']: row for row in _rows(db, '''SELECT DISTINCT s.id,s.url,s.text FROM demand_sources s
        JOIN demand_candidates c ON c.source_id=s.id WHERE c.cycle_id=? AND c.state='read' ''', (cycle_id,))
            if row['id'] in allowed}


def finish_task(db, task_id, result, source_ids, valid_source_ids, status='done'):
    row = task(db, task_id)
    if status not in ('done', 'failed') or not isinstance(result, dict) or len(_json(result)) > 100000:
        raise ValueError('agent_result_invalid')
    if not isinstance(source_ids, list) or any(not isinstance(item, str) for item in source_ids):
        raise ValueError('agent_result_sources_invalid')
    sources = _read_sources(db, row['cycle_id'], valid_source_ids)
    if not set(source_ids).issubset(sources):
        raise ValueError('agent_sources_not_read_in_cycle')
    identities = sorted(set(source_ids))
    if row['status'] in ('done', 'failed'):
        if row['status'] == status and row['result'] == result and row['source_ids'] == identities:
            return row
        raise ValueError('agent_result_is_immutable')
    if row['status'] != 'running':
        raise ValueError('agent_task_not_running')
    db.execute('UPDATE demand_agent_tasks SET status=?,result=?,source_ids=?,updated=? WHERE id=?',
               (status, _json(result), _json(identities), _now(), task_id))
    _event(db, row['cycle_id'], task_id, status, {'source_ids': identities})
    return task(db, task_id)


def validate_child_proposal(payload, sources):
    """Pure admission guard shared by bounded repair and actual child insertion."""
    _validate(payload, CHILD_SCHEMA)
    _substantive(payload)
    if not set(payload['source_ids']).issubset(sources):
        raise ValueError('agent_child_sources_not_read')
    if len(payload['trigger_quote'].split()) > 25:
        raise ValueError('agent_child_quote_too_long')
    quote = payload['trigger_quote'].strip()
    quote_normalized = False
    if not any(_literal(quote) in _literal(sources[identity]['text']) for identity in payload['source_ids']):
        wrappers = (('«', '»'), ('“', '”'), ('„', '“'), ('"', '"'), ("'", "'"))
        unwrapped = quote[1:-1].strip() if any(quote.startswith(left) and quote.endswith(right)
                                             for left, right in wrappers) else ''
        if not unwrapped or not any(_literal(unwrapped) in _literal(sources[identity]['text'])
                                    for identity in payload['source_ids']):
            raise ValueError('agent_child_quote_not_found')
        payload = {**payload, 'trigger_quote': unwrapped}
        _validate(payload, CHILD_SCHEMA)
        quote_normalized = True
    urls = {sources[identity]['url'].rstrip('/') for identity in payload['source_ids']}
    for field in ('question', 'scope', 'methods', 'why_needed', 'support_condition', 'refute_condition'):
        if any(url.rstrip('.,;)]}/') not in urls for url in re.findall(r'https?://[^\s<>"\']+', payload[field])):
            raise ValueError('agent_child_url_not_in_sources')
    return payload, quote_normalized


def enqueue_children(db, parent_task_id, proposals, valid_source_ids, synthesis_round=None, defer_reason=None):
    """Only the coordinator calls this after reviewing a specialist's proposal."""
    parent = task(db, parent_task_id)
    if plan(db, parent['cycle_id'])['status'] in ('complete', 'limited'):
        raise ValueError('agent_plan_finalized')
    if parent['status'] not in ('running', 'done'):
        raise ValueError('agent_parent_not_researched')
    if not isinstance(proposals, list) or len(proposals) > 8:
        raise ValueError('agent_child_proposals_invalid')
    if defer_reason not in (None, 'orchestrator_deferred'):
        raise ValueError('agent_defer_reason_invalid')
    sources = _read_sources(db, parent['cycle_id'], valid_source_ids)
    output = {'admitted': [], 'deferred': [], 'existing': [], 'rejected': []}
    for index, payload in enumerate(proposals):
        quote_normalized = False
        try:
            payload, quote_normalized = validate_child_proposal(payload, sources)
        except ValueError as error:
            reason = str(error)
            brief = {key: payload[key][:maximum] for key, maximum in (('key', 100), ('role', 40), ('question', 1000))
                     if isinstance(payload, dict) and isinstance(payload.get(key), str)}
            rejected = {'index': index, 'reason': reason, 'proposal': brief}
            output['rejected'].append(rejected)
            _event(db, parent['cycle_id'], parent_task_id, 'child_rejected', rejected)
            continue
        if quote_normalized:
            _event(db, parent['cycle_id'], parent_task_id, 'child_quote_normalized',
                   {'key': payload['key'], 'source_ids': payload['source_ids'],
                    'normalization': 'one_outer_quote_pair; inner_text_verified_verbatim'})
        question_key = hashlib.sha256(_norm(payload['question']).encode()).hexdigest()
        # A repeated proposal label is not evidence that its research question
        # was already assigned. Only the normalized question establishes that.
        existing = _rows(db, 'SELECT id FROM demand_agent_tasks WHERE cycle_id=? AND question_key=?',
                         (parent['cycle_id'], question_key))
        if existing:
            output['existing'].append(existing[0]['id'])
            _event(db, parent['cycle_id'], parent_task_id, 'child_duplicate', {'existing': existing[0]['id']})
            continue
        tasks = _rows(db, 'SELECT id,parent_task_id,status,ordinal FROM demand_agent_tasks WHERE cycle_id=?', (parent['cycle_id'],))
        admitted = [item for item in tasks if item['status'] != 'deferred']
        reason = defer_reason
        if reason:
            pass
        elif synthesis_round is not None and synthesis_round >= MAX_SYNTHESIS_ROUNDS:
            reason = 'synthesis_round_limit'
        elif parent['depth'] >= MAX_DEPTH:
            reason = 'depth_limit'
        elif index >= 2 or sum(item['parent_task_id'] == parent_task_id for item in admitted) >= MAX_CHILDREN_PER_PARENT:
            reason = 'parent_child_limit'
        elif len(admitted) >= MAX_TASKS or sum(item['parent_task_id'] is not None for item in admitted) >= MAX_CHILDREN:
            reason = 'cycle_child_limit'
        identity = _insert_task(db, parent['cycle_id'], payload, max(item['ordinal'] for item in tasks) + 1,
                                parent, 'deferred' if reason else 'pending', reason)
        output['deferred' if reason else 'admitted'].append(identity)
        _event(db, parent['cycle_id'], identity, 'child_deferred' if reason else 'child_admitted',
               {'parent_task_id': parent_task_id, 'reason': reason, 'review_needed': bool(reason)})
    return output


def snapshot(db, cycle_id):
    from iva_integration.demand_retention import archived
    frozen = archived(db, cycle_id)
    if frozen and frozen['snapshot'] is not None:
        return frozen['snapshot']
    current = plan(db, cycle_id)
    tasks = [_decoded(row) for row in _rows(db, 'SELECT * FROM demand_agent_tasks WHERE cycle_id=? ORDER BY ordinal,id', (cycle_id,))]
    syntheses = [{**row, 'result': json.loads(row['result'])} for row in _rows(db,
                 'SELECT * FROM demand_agent_syntheses WHERE cycle_id=? ORDER BY round', (cycle_id,))]
    def child_gaps(result):
        result = result or {}
        children = result.get('children') or {}
        return bool(result.get('child_limitations') or children.get('rejected') or
                    (children.get('coordinator_deferred') or {}).get('rejected'))
    unresolved = [row['id'] for row in tasks if row['status'] in ('deferred', 'failed') or child_gaps(row['result'])]
    synthesis_failed = any(row['status'] == 'failed' for row in syntheses)
    synthesis_child_gaps = any(child_gaps(row.get('result')) for row in syntheses)
    return {'plan': current, 'tasks': tasks, 'syntheses': syntheses,
            'counts': {state: sum(row['status'] == state for row in tasks)
                       for state in ('pending', 'running', 'done', 'failed', 'deferred')},
            'limits': {'root_researchers': 4, 'max_tasks': MAX_TASKS, 'max_children': MAX_CHILDREN,
                       'max_children_per_parent': MAX_CHILDREN_PER_PARENT, 'max_depth': MAX_DEPTH,
                       'max_synthesis_rounds': MAX_SYNTHESIS_ROUNDS},
            'ready_for_synthesis': bool(tasks) and not any(row['status'] in ('pending', 'running') for row in tasks),
            'review_needed': unresolved + (['synthesis_child_grounding'] if synthesis_child_gaps else []),
            'limited': bool(unresolved or synthesis_failed or synthesis_child_gaps),
            'synthesis_failed': synthesis_failed,
            'limitations': ['Roles run sequentially, not as concurrent processes.',
                            'Source counts and completed assignments do not establish willingness to pay.',
                            'Deferred and failed tests require explicit follow-up; they are not completed evidence.']}


def save_synthesis(db, cycle_id, result, status='final', review_round=None):
    if status not in ('review', 'final', 'failed') or not isinstance(result, dict) or len(_json(result)) > 100000:
        raise ValueError('agent_synthesis_invalid')
    view = snapshot(db, cycle_id)
    if not view['plan'] or (status in ('final', 'failed') and not view['ready_for_synthesis']):
        raise ValueError('agent_assignments_unfinished')
    if review_round is None:
        review_round = max((row['round'] for row in view['syntheses']), default=0) + 1
    if isinstance(review_round, bool) or review_round not in (1, 2):
        raise ValueError('agent_synthesis_round_limit')
    existing = [row for row in view['syntheses'] if row['round'] == review_round]
    if existing:
        if existing[0]['status'] == status and existing[0]['result'] == result:
            return existing[0]
        raise ValueError('agent_synthesis_is_immutable')
    if view['syntheses'] and view['syntheses'][-1]['status'] in ('final', 'failed'):
        raise ValueError('agent_synthesis_already_final')
    if review_round != len(view['syntheses']) + 1:
        raise ValueError('agent_synthesis_round_gap')
    db.execute('INSERT INTO demand_agent_syntheses VALUES(?,?,?,?,?)',
               (cycle_id, review_round, status, _json(result), _now()))
    children = result.get('children') or {}
    result_gaps = result.get('child_limitations') or children.get('rejected') or (children.get('coordinator_deferred') or {}).get('rejected')
    state = 'limited' if view['limited'] or status == 'failed' or result_gaps else 'complete'
    db.execute('UPDATE demand_agent_plans SET status=? WHERE cycle_id=?',
               (state if status in ('final', 'failed') else 'reviewing', cycle_id))
    _event(db, cycle_id, None, 'synthesis_' + status, {'round': review_round, 'review_needed': view['review_needed']})
    return next(row for row in snapshot(db, cycle_id)['syntheses'] if row['round'] == review_round)
