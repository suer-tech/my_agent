"""One real planner-step proof of the research reasoning floor, not a full cycle.

Run only after installing the fix, from /opt/iva. The fixed isolated database is
fresh and is never resumed by this helper. No discovery, source fetching, memory
export, scheduled job, notification, financial write or public action is run.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.verify_demand_agents import TASK, deployment_read_lock, stamp
from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
from iva_integration import demand_agents as agents
from iva_integration import demand_research as research
from iva_integration.demand_sources import ResearchAnalysisCodex

RELEASE = ROOT / "data/demand-release-20261025-agents-quality"
OUTPUT = RELEASE / "verification-quality"
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


def paths(release=RELEASE, output=OUTPUT):
    release, output = Path(release), Path(output)
    if output != release / "verification-quality":
        raise ValueError("unexpected_quality_directory")
    if not release.is_dir():
        raise ValueError("quality_release_missing")
    for path in (release.parent, release, output):
        if path.is_symlink():
            raise ValueError("quality_path_is_symlink")
    output.mkdir(mode=0o700, exist_ok=True)
    for name in ("research.sqlite3", "research.sqlite3-wal", "research.sqlite3-shm",
                 "research.sqlite3-journal", "cards", "state.json", "quality-report.json",
                 ".verification.lock", "progress.jsonl", "priority-status.json", "priority-status.json.tmp"):
        path = output / name
        if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()):
            raise ValueError("quality_path_is_symlink")
    return output / "research.sqlite3", output / "cards", output / "state.json"


def fingerprints(root, home):
    """Hash shared settings without printing their contents or authorization."""
    settings = {"iva_model": Path(root) / "data/iva-model.json",
                "iva_agents": Path(root) / "data/iva-agents.json",
                "codex_config": Path(home) / "config.toml"}
    result = {}
    for name, path in settings.items():
        if not path.exists():
            result[name] = {"exists": False, "sha256": None}
        else:
            result[name] = {"exists": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def prepare(database, cards, state_path):
    if database.exists() or state_path.exists():
        raise RuntimeError("quality_probe_requires_fresh_database")
    job_id = "diagnostic-quality-" + hashlib.sha256(str(database).encode()).hexdigest()[:16]
    store = research.Research(database, cards)
    try:
        cycle = store.cycle(job_id, TASK)
        if cycle["stage"] != "agent_plan":
            raise RuntimeError("quality_probe_expected_agent_plan")
        state = {"diagnostic": "single-real-planner-step-v1", "job_id": job_id,
                 "cycle_id": cycle["id"], "task": TASK, "created_at": stamp(),
                 "full_cycle": False, "seeded_sources": False, "steps": 0}
        atomic_json(state_path, state)
        return state
    finally:
        store.close()


def validated_plan(snapshot, events):
    plan = snapshot.get("plan") or {}
    try:
        payload = {key: plan[key] for key in agents.PLAN_SCHEMA["required"]}
        agents._validate(payload, agents.PLAN_SCHEMA)
        for item in payload["researchers"]:
            agents._substantive(item)
        if any(len(set(agents._norm(payload[field]).split())) < 6
               for field in ("objective", "decision", "rationale")):
            return False
        if {item["role"] for item in payload["researchers"]} != set(agents.ROLES):
            return False
        if any(len({agents._norm(item[field]) for item in payload["researchers"]}) != 4
               for field in ("key", "question", "scope")):
            return False
        return any(row["event"] == "plan_validated" for row in events)
    except (ValueError, TypeError, KeyError):
        return False


def run_planner(state, database, cards, client):
    """Observe the planner and its independent scope audit, including each runtime."""
    started = time.monotonic()
    calls = []
    structured = client.structured
    def observe(prompt, schema):
        properties = schema.get('properties', {})
        record = {'kind': 'planner' if 'researchers' in properties else
                         'scope_audit' if 'scope_decisions' in properties else 'unexpected',
                  'completed': False}
        calls.append(record)
        try:
            value = structured(prompt, schema)
            record['completed'] = True
            return value
        finally:
            execution = getattr(client, 'last_execution', {}) or {}
            record['execution'] = {key: execution[key] for key in
                ('model', 'reasoning_effort', 'web_search', 'read_only') if key in execution}
            record['web_event_count'] = len(getattr(client, 'last_web_events', None) or [])
    with patch.object(client, "structured", side_effect=observe) as observed:
        result = research.run_step(state["job_id"], state["task"], db_path=database,
                                   cards_dir=cards, cli=client, cycle_id=state["cycle_id"])
    store = research.Research(database, cards)
    try:
        snapshot = agents.snapshot(store.db, state["cycle_id"])
        events = store.rows("SELECT event FROM demand_agent_events WHERE cycle_id=? ORDER BY id", (state["cycle_id"],))
        scope_checked = store.rows("SELECT id FROM demand_events WHERE cycle_id=? AND event='scope_plan_checked'", (state["cycle_id"],))
        sources = store.rows("SELECT COUNT(*) AS n FROM demand_sources")[0]["n"]
    finally:
        store.close()
    execution = getattr(client, "last_execution", {}) or {}
    criteria = {"planner_and_independent_scope_audit_completed":
                    [row['kind'] for row in calls] == ['planner', 'scope_audit']
                    and all(row['completed'] for row in calls) and len(scope_checked) == 1,
                "actual_effort_at_least_medium": bool(calls) and all(
                    row['execution'].get('reasoning_effort') in EFFORTS and
                    EFFORTS.index(row['execution']['reasoning_effort']) >= EFFORTS.index('medium') for row in calls),
                "read_only_tools_disabled": bool(calls) and all(
                    row['execution'].get('read_only') is True and row['execution'].get('web_search') == 'disabled'
                    and row['web_event_count'] == 0 for row in calls),
                "schema_and_substantive_plan_validated": validated_plan(snapshot, events),
                "only_planner_step_completed": result.get("ok") is True and result.get("has_more") is True
                    and result.get("stage") == "discovery" and sources == 0}
    return {"diagnostic": "single-real-planner-step-v1", "at": stamp(),
            "cycle_id": state["cycle_id"], "elapsed_seconds": round(time.monotonic() - started, 2),
            "full_cycle": False, "structured_call_count": observed.call_count,
            "structured_calls": calls,
            "execution": {key: execution[key] for key in ("model", "reasoning_effort", "web_search", "read_only") if key in execution},
            "criteria": criteria, "passed": all(criteria.values()), "plan": snapshot.get("plan"),
            "tasks": snapshot.get("tasks"), "result": result,
            "limitation": "Only one planner step with its independent scope audit was tested (two model calls). Discovery, researchers, child execution, synthesis and memory export were deliberately not run; this is not a completed research cycle.",
            "writes": "Isolated quality-probe database/state/report and normal shared research lease only; no notifications, owner memory or financial writes."}


def main():
    if len(sys.argv) != 1 or os.name != "posix" or ROOT != Path("/opt/iva"):
        raise RuntimeError("quality_probe_run_only_on_vps_without_arguments")
    database, cards, state_path = paths()
    with deployment_read_lock(), exclusive(OUTPUT):
        from iva_integration.run_pilot import env as server_env
        from iva_integration import research_priority
        os.environ.update(server_env())
        helper = Path("/usr/local/bin/iva-codex")
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise RuntimeError("vpn_codex_helper_unavailable")
        os.environ["FINANCE_CODEX_EXE"] = str(helper)
        client = ResearchAnalysisCodex(timeout=75, role="orchestrator")
        before = fingerprints(ROOT, client.original_home)
        state = prepare(database, cards, state_path)
        with patch.object(research_priority, "STATUS", OUTPUT / "priority-status.json"), research_lease(state, OUTPUT):
            value = run_planner(state, database, cards, client)
        after = fingerprints(ROOT, client.original_home)
        value["config_fingerprints"] = {"before": before, "after": after}
        value["criteria"]["shared_settings_unchanged"] = before == after
        value["passed"] = all(value["criteria"].values())
        state["steps"] = 1
        state["completed_at"] = stamp()
        state["probe_finished_full_cycle_not_run"] = True
        atomic_json(state_path, state)
        atomic_json(OUTPUT / "quality-report.json", value)
        print(json.dumps({key: value[key] for key in ("diagnostic", "cycle_id", "passed", "execution", "criteria", "limitation")}, ensure_ascii=False), flush=True)
        return 0 if value["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"diagnostic": "single-real-planner-step-v1", "passed": False,
                          "error_type": type(error).__name__}), flush=True)
        raise SystemExit(2)
