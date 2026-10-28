"""Persistent, source-grounded research questions; no model or network access.

All writes use the caller's SQLite transaction. Public source text is evidence,
never authority to act. Deferred questions remain visible; inconclusive research
is parked rather than repeatedly launched without a new decision.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

MAX_OPEN_PER_THEME = 8
MAX_DEPTH = 3
MAX_FOLLOWUPS = 2
PRIORITIES = {"critical": 0, "high": 1, "normal": 2}
KINDS = ["buyer_problem", "competitor_reviews", "adoption_request", "foreign_market",
         "alternatives", "economics", "counterevidence"]


def _string(maximum=1000):
    return {"type": "string", "maxLength": maximum}


SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "question_key": _string(160), "question": _string(), "query": _string(600),
    "reason": _string(), "priority": {"type": "string", "enum": list(PRIORITIES)},
    "kind": {"type": "string", "enum": KINDS},
    "geography": {"type": "string", "enum": ["domestic", "international"]},
    "region": _string(160), "subject": _string(160),
    "pass_condition": _string(), "fail_condition": _string(),
    "source_ids": {"type": "array", "items": _string(100), "minItems": 1, "maxItems": 6},
}}
SCHEMA["required"] = list(SCHEMA["properties"])


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _norm(value):
    return " ".join(re.sub(r"[^\w]+", " ", str(value).casefold(), flags=re.UNICODE).split())


def _rows(db, sql, params=()):
    cursor = db.execute(sql, params)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS demand_questions (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, track TEXT NOT NULL,
        topic_key TEXT NOT NULL, stable_key TEXT NOT NULL, parent_id TEXT,
        hypothesis_id TEXT, depth INTEGER NOT NULL, state TEXT NOT NULL,
        priority TEXT NOT NULL, created_cycle_id TEXT NOT NULL, active_cycle_id TEXT,
        payload TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
        result TEXT, UNIQUE(job_id,track,topic_key,stable_key))""")
    db.execute("""CREATE TABLE IF NOT EXISTS demand_question_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, question_id TEXT NOT NULL,
        event TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS demand_questions_queue ON demand_questions(job_id,track,state,priority,created)")


ensure_schema = ensure


def _event(db, identity, event, detail):
    db.execute("INSERT INTO demand_question_events(question_id,event,detail,created) VALUES(?,?,?,?)",
               (identity, event, _json(detail), _now()))


def _row(db, identity):
    rows = _rows(db, "SELECT * FROM demand_questions WHERE id=?", (identity,))
    if not rows:
        raise ValueError("question_not_found")
    return rows[0]


def _decoded(row):
    payload = json.loads(row["payload"])
    return {**row, **payload, "payload": payload, "parent_question_id": row["parent_id"],
            "result": json.loads(row["result"]) if row["result"] else None}


def _cycle(db, identity, job_id, track):
    rows = _rows(db, "SELECT job_id,track FROM demand_cycles WHERE id=?", (identity,))
    if not rows or rows[0]["job_id"] != job_id or rows[0]["track"] != track:
        raise ValueError("cycle_scope_mismatch")


def _sources(db, cycle_id, allowed=None):
    rows = _rows(db, """SELECT DISTINCT s.id,s.url,s.text FROM demand_sources s
        JOIN demand_candidates c ON c.source_id=s.id
        WHERE c.cycle_id=? AND c.state='read'""", (cycle_id,))
    allowed_set = set(allowed) if allowed is not None else None
    return {row["id"]: row for row in rows if allowed_set is None or row["id"] in allowed_set}


def _validate(proposal, sources):
    if not isinstance(proposal, dict) or set(proposal) != set(SCHEMA["required"]):
        raise ValueError("question_shape_invalid")
    for key, schema in SCHEMA["properties"].items():
        value = proposal[key]
        if schema["type"] == "string":
            if not isinstance(value, str) or (not value.strip() and key != "subject"):
                raise ValueError("question_field_empty")
            if len(value) > schema.get("maxLength", 1000) or "\x00" in value:
                raise ValueError("question_field_too_long")
            if "enum" in schema and value not in schema["enum"]:
                raise ValueError("question_field_invalid")
    identities = proposal["source_ids"]
    if (not isinstance(identities, list) or not 1 <= len(identities) <= 6
            or any(not isinstance(identity, str) or identity not in sources for identity in identities)):
        raise ValueError("question_sources_not_read_in_cycle")
    # Names must remain literal: punctuation can distinguish C++, C# and C,
    # or two different product names. Token normalization is only for dedupe.
    subject = " ".join(proposal["subject"].casefold().split())
    if subject and not any(subject in " ".join(sources[identity]["text"].casefold().split()) for identity in identities):
        raise ValueError("question_subject_not_in_sources")
    urls = {sources[identity]["url"].rstrip("/") for identity in identities}
    for key in ("question", "query", "reason", "pass_condition", "fail_condition"):
        for url in re.findall(r"https?://[^\s<>\"']+", proposal[key]):
            if url.rstrip(".,;)]}/") not in urls:
                raise ValueError("question_url_not_in_sources")
    if _norm(proposal["pass_condition"]) == _norm(proposal["fail_condition"]):
        raise ValueError("question_criteria_not_distinct")
    if not _norm(proposal["question_key"]):
        raise ValueError("question_key_invalid")


def _rebalance(db, job_id, track, topic_key):
    rows = _rows(db, """SELECT id,state,priority,created FROM demand_questions
        WHERE job_id=? AND track=? AND topic_key=? AND state IN ('pending','running','deferred')""",
        (job_id, track, topic_key))
    running = sum(row["state"] == "running" for row in rows)
    queued = sorted((row for row in rows if row["state"] != "running"),
                    key=lambda row: (PRIORITIES[row["priority"]], row["created"], row["id"]))
    for index, row in enumerate(queued):
        state = "pending" if index < max(0, MAX_OPEN_PER_THEME - running) else "deferred"
        if state != row["state"]:
            db.execute("UPDATE demand_questions SET state=?,updated=? WHERE id=?", (state, _now(), row["id"]))


def enqueue(db, job_id, track, cycle_id, hypothesis_id, followups, valid_source_ids,
            parent_question_id=None):
    """Queue at most two grounded questions; preserve previously declared criteria.

    valid_source_ids can be a set or a mapping. Membership is additionally checked
    against actual read sources in the supplied cycle; a fabricated mapping cannot
    confer provenance. Named subjects must occur in those source snapshots.
    """
    ensure(db)
    _cycle(db, cycle_id, job_id, track)
    parent = _row(db, parent_question_id) if parent_question_id else None
    if parent and (parent["job_id"] != job_id or parent["track"] != track):
        raise ValueError("parent_scope_mismatch")
    if hypothesis_id:
        found = _rows(db, "SELECT job_id,track FROM demand_hypotheses WHERE id=?", (hypothesis_id,))
        if not found or found[0]["job_id"] != job_id or found[0]["track"] != track:
            raise ValueError("hypothesis_scope_mismatch")
    sources = _sources(db, cycle_id, valid_source_ids)
    result = {"queued": [], "existing": [], "deferred": [], "rejected": []}
    if not isinstance(followups, list):
        raise ValueError("followups_must_be_list")
    touched = set()
    created = []
    for index, proposed in enumerate(followups):
        if index >= MAX_FOLLOWUPS:
            result["rejected"].append({"index": index, "reason": "followup_batch_limit"})
            continue
        try:
            _validate(proposed, sources)
        except ValueError as error:
            result["rejected"].append({"index": index, "reason": str(error)})
            continue
        payload = {key: value.strip() if isinstance(value, str) else list(dict.fromkeys(value))
                   for key, value in proposed.items()}
        topic_key = parent["topic_key"] if parent else (hypothesis_id or _norm(payload["subject"]) or track)
        scope = [payload["question_key"], payload["geography"], payload["region"], payload["subject"]]
        stable = hashlib.sha256(_json([_norm(item) for item in scope]).encode()).hexdigest()[:32]
        duplicate = _rows(db, "SELECT id FROM demand_questions WHERE job_id=? AND track=? AND topic_key=? AND stable_key=?",
                          (job_id, track, topic_key, stable))
        if duplicate:
            result["existing"].append(duplicate[0]["id"])
            continue
        depth = parent["depth"] + 1 if parent else 1
        state = "needs_owner" if depth > MAX_DEPTH else "pending"
        identity, stamp = uuid.uuid4().hex[:16], _now()
        db.execute("""INSERT INTO demand_questions
            (id,job_id,track,topic_key,stable_key,parent_id,hypothesis_id,depth,state,priority,
             created_cycle_id,active_cycle_id,payload,created,updated,result)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,NULL)""",
            (identity, job_id, track, topic_key, stable, parent_question_id, hypothesis_id or None,
             depth, state, payload["priority"], cycle_id, _json(payload), stamp, stamp))
        _event(db, identity, "queued" if state == "pending" else "depth_limit", {"source_ids": payload["source_ids"]})
        touched.add(topic_key)
        created.append(identity)
    for topic_key in touched:
        _rebalance(db, job_id, track, topic_key)
    for identity in created:
        state = _row(db, identity)["state"]
        result["queued" if state == "pending" else "deferred"].append(identity)
    return result


def pick(db, job_id, track=None):
    """Read-only selection. Only start() claims a question for a concrete cycle."""
    sql = "SELECT * FROM demand_questions WHERE job_id=? AND state='pending'"
    params = [job_id]
    if track is not None:
        sql += " AND track=?"
        params.append(track)
    rows = _rows(db, sql + " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,created,id LIMIT 1", params)
    return _decoded(rows[0]) if rows else None


select_pending = pick


def start(db, identity, cycle_id):
    row = _row(db, identity)
    _cycle(db, cycle_id, row["job_id"], row["track"])
    if row["state"] == "running" and row["active_cycle_id"] == cycle_id:
        return _decoded(row)
    if row["state"] != "pending":
        raise ValueError("question_not_pending")
    changed = db.execute("UPDATE demand_questions SET state='running',active_cycle_id=?,updated=? WHERE id=? AND state='pending'",
                         (cycle_id, _now(), identity)).rowcount
    if changed != 1:
        raise ValueError("question_claim_conflict")
    _event(db, identity, "started", {"cycle_id": cycle_id})
    return _decoded(_row(db, identity))


def complete(db, identity, verdict, basis, evidence_ids, valid_source_ids=None):
    row = _row(db, identity)
    if verdict not in ("supported", "contradicted", "inconclusive"):
        raise ValueError("question_verdict_invalid")
    if not isinstance(basis, str) or not basis.strip() or len(basis) > 2000:
        raise ValueError("question_basis_invalid")
    if not isinstance(evidence_ids, list) or len(evidence_ids) > 20 or any(not isinstance(item, str) for item in evidence_ids):
        raise ValueError("question_evidence_invalid")
    evidence_ids = list(dict.fromkeys(evidence_ids))
    sources = _sources(db, row["active_cycle_id"], valid_source_ids) if row["active_cycle_id"] else {}
    if any(identity not in sources for identity in evidence_ids) or (verdict != "inconclusive" and not evidence_ids):
        raise ValueError("question_result_sources_not_read_in_assigned_cycle")
    result = {"verdict": verdict, "basis": basis.strip(), "source_ids": evidence_ids,
              "cycle_id": row["active_cycle_id"]}
    if row["state"] in ("supported", "contradicted", "parked") and row["result"] == _json(result):
        return _decoded(row)
    if row["state"] != "running":
        raise ValueError("question_not_running")
    state = "parked" if verdict == "inconclusive" else verdict
    changed = db.execute("UPDATE demand_questions SET state=?,result=?,updated=? WHERE id=? AND state='running' AND active_cycle_id=?",
                         (state, _json(result), _now(), identity, row["active_cycle_id"])).rowcount
    if changed != 1:
        raise ValueError("question_completion_conflict")
    _event(db, identity, "completed", result)
    _rebalance(db, row["job_id"], row["track"], row["topic_key"])
    return _decoded(_row(db, identity))


finish = complete


def summary(db, job_id=None, limit=20):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("question_limit_invalid")
    ensure(db)
    where, params = (" WHERE job_id=?", [job_id]) if job_id is not None else ("", [])
    counts = _rows(db, "SELECT state,count(*) count FROM demand_questions" + where + " GROUP BY state", params)
    rows = _rows(db, "SELECT * FROM demand_questions" + where +
                 " ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'pending' THEN 1 WHEN 'deferred' THEN 2 ELSE 3 END,"
                 "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,created,id LIMIT ?", params + [limit])
    return {"counts": {row["state"]: row["count"] for row in counts}, "questions": [_decoded(row) for row in rows],
            "limitations": "Истории источников не являются числом людей; авторская независимость неизвестна. Это вопросы кабинетного исследования, не разрешение на обращения или покупки."}


listing = summary
