"""Read-only evidence adapter for confirmed Threads reader repairs.

``root`` is the Threads data directory, normally /opt/iva/data/threads-browser.
The caller must atomically persist ``state`` with its durable notification outbox.
Events intentionally retain a stable ID across polls; delivery deduplication belongs
to that outbox. No queue, browser, Telegram or application state is changed here.
"""
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3


_FOLDERS = {"inbox", "requests", "hidden"}
_CAMPAIGNS = {"extension", "development"}
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAX_JSON = 65536


def _stamp(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value) if 0 < value < 1e12 else None


def _object(text):
    if not isinstance(text, str) or len(text) > _MAX_JSON:
        return {}
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _inbox(root, now):
    path = root / "inbox-status.json"
    try:
        if path.stat().st_size > _MAX_JSON:
            return None
        value = _object(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None
    checked = _stamp(value.get("checked_at"))
    finished = _stamp(value.get("finished_at"))
    success = _stamp(value.get("last_success_at"))
    folders = value.get("folders")
    checked_folders = value.get("checked_folders")
    if (value.get("state") != "ok" or checked is None or finished is None
            or success is None or not checked <= success <= finished <= now
            or not isinstance(folders, list) or not isinstance(checked_folders, list)
            or any(not isinstance(item, str) for item in folders + checked_folders)
            or set(folders) != _FOLDERS or set(checked_folders) != _FOLDERS):
        return None
    return {"checked_at": checked, "finished_at": finished}


def collect(root: Path, previous: dict | None, now: float) -> dict:
    """Return safe, positively verified events, without replaying pre-install history.

    The first successful or unavailable-source poll establishes a time baseline and
    emits nothing. A repair already active at that time can produce an event when
    it is subsequently verified. Missing/corrupt proof fails closed. Mere incident
    disappearance, tests, installation, and partial inbox scans never suffice.
    """
    stamp = _stamp(now)
    if stamp is None:
        raise ValueError("Invalid recovery collection time")
    prior = previous if isinstance(previous, dict) else {}
    baseline = _stamp(prior.get("baseline_at")) if prior.get("version") == 1 else None
    baseline = baseline if baseline is not None and baseline <= stamp else stamp
    state = {"version": 1, "baseline_at": baseline}
    output = {"state": state, "events": [], "diagnostics": {"threads": "baseline"}}
    if previous is None or baseline == stamp:
        return output

    root = Path(root)
    path = root / "repair" / "repair.sqlite3"
    if not path.is_file():
        output["diagnostics"]["threads"] = "source_unavailable"
        return output
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            # The observation ties the mutable verification to its latest episode.
            # An older repair is not advertised when the same failure has recurred.
            rows = db.execute("""
                SELECT j.id, j.campaign, j.created, j.updated, j.result
                FROM jobs AS j JOIN observations AS o
                  ON o.campaign=j.campaign AND o.incident=j.incident
                 AND o.code=j.code AND o.last_job=j.id AND o.active=0
                WHERE j.code='messages' AND j.state='verified'
                  AND j.updated > ? AND j.updated <= ?
                  AND length(j.result) <= ?
                ORDER BY j.updated, j.id
                """, (baseline, stamp, _MAX_JSON)).fetchall()
    except (sqlite3.Error, OSError):
        output["diagnostics"]["threads"] = "source_unavailable"
        return output
    if not rows:
        output["diagnostics"]["threads"] = "no_verified_repair"
        return output
    output["diagnostics"]["threads"] = "proof_unavailable"
    inbox = _inbox(root, stamp)
    if inbox is None:
        return output
    for row in rows:
        ident, campaign = row["id"], row["campaign"]
        if not isinstance(ident, str) or not _JOB_ID.fullmatch(ident) or campaign not in _CAMPAIGNS:
            continue
        result = _object(row["result"])
        installed = _stamp(result.get("installed_at"))
        verified = _stamp(result.get("verified_at"))
        created = _stamp(row["created"])
        updated = _stamp(row["updated"])
        if (result.get("installed") is not True or result.get("tests_passed") is not True
                or result.get("reason") != "live_inbox_check_passed"
                or None in (installed, verified, created, updated)
                or not created <= installed < verified <= updated <= stamp
                or verified <= baseline or inbox["checked_at"] <= installed):
            continue
        output["events"].append({
            "id": "threads:code_patch:" + ident,
            "source": "threads", "code": "messages", "action": "code_patch",
            "campaign": campaign, "verified_at": verified,
            "proof": {"job_id": ident, "installed": True, "tests_passed": True,
                      "installed_at": installed, "inbox_checked_at": inbox["checked_at"],
                      "inbox_finished_at": inbox["finished_at"],
                      "all_folders_checked": True, "latest_incident_job": True,
                      "incident_inactive": True},
        })
    if output["events"]:
        output["diagnostics"]["threads"] = "read_ok"
    return output
