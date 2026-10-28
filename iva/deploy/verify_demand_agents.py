"""Real research-agent diagnostic in a fixed, isolated verification directory.

Uses the production research code, native models and public HTTP reader. Never
dispatches notifications or writes the financial database or the owner's vault.
The shared browser coordinator lease is acquired and released normally.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
from iva_integration import demand_research as research

RELEASE = ROOT / "data/demand-release-20261025-agents"
OUTPUT = RELEASE / "verification"
ROLES = {"buyer_problem", "alternatives", "foreign", "critic"}
TASK = """Исследовать публичные свидетельства неудовлетворённого спроса на ПО для
небольших российских продавцов Ozon/Wildberries: расхождения остатков и заказов
между маркетплейсами и МойСклад. Не утверждать заранее, что проблема существует
или что текущие интеграции её не решают. Оркестратор должен спланировать четыре
самостоятельные проверки: исходные проблемы/обходы/оплата, существующие
альтернативы и их внедрение, зарубежный опыт с отдельной проверкой применимости,
независимая контрпроверка и экономика. Если действительно прочитанные материалы
выявят конкретное противоречие или существенный пробел, поручить узкую проверку
дочернему исследователю и учесть ответ. Не создавать ветку ради числа агентов.
Итог должен различать факты, гипотезы и неизвестное; ссылки и короткие точные
цитаты обязательны. Только публичные чтения: без контактов, публикаций, покупок,
авторизации на сайтах или обращения к личным данным владельца."""


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(value, directory=OUTPUT):
    text = json.dumps({"at": stamp(), **value}, ensure_ascii=False, sort_keys=True)
    print(text, flush=True)
    with (directory / "progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(text + "\n")


def paths(release=RELEASE, output=OUTPUT):
    """No caller-selected live database/vault paths; refuse symlink escapes."""
    release, output = Path(release), Path(output)
    if output != release / "verification":
        raise ValueError("unexpected_diagnostic_directory")
    for candidate in (release.parent, release, output):
        if candidate.is_symlink():
            raise ValueError("diagnostic_path_is_symlink")
    if not release.is_dir():
        raise ValueError("diagnostic_release_missing")
    output.mkdir(mode=0o700, exist_ok=True)
    children = [output / name for name in ("research.sqlite3", "cards", "state.json", "vault")]
    generated = [output / name for name in (
        "research.sqlite3-wal", "research.sqlite3-shm", "research.sqlite3-journal",
        ".verification.lock", "model-calls.jsonl", "source-fetches.jsonl", "progress.jsonl", "final-report.json",
        "memory-status.json", "priority-status.json", "priority-status.json.tmp")]
    for child in children + generated:
        if child.is_symlink() or not child.resolve().is_relative_to(output.resolve()):
            raise ValueError("diagnostic_path_is_symlink")
    return children


@contextmanager
def deployment_read_lock(path=ROOT / "data/application-deploy.lock"):
    """Prevent a diagnostic from starting while an application release copies code."""
    import fcntl
    if path.is_symlink():
        raise RuntimeError("diagnostic_deployment_lock_is_symlink")
    with path.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("application_deployment_in_progress") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def agent_snapshot(store, cycle_id):
    from iva_integration import demand_agents
    return demand_agents.snapshot(store.db, cycle_id)


def prepare(database, cards, state_path):
    identity = "diagnostic-agents-" + hashlib.sha256(str(database).encode()).hexdigest()[:16]
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state.get("job_id") != identity or state.get("database") != str(database)
                or state.get("task") != TASK or state.get("diagnostic") != "real-agents-v1"):
            raise ValueError("invalid_diagnostic_resume_state")
        return state
    store = research.Research(database, cards)
    try:
        cycle = store.cycle(identity, TASK)
        state = {"diagnostic": "real-agents-v1", "job_id": identity,
                 "cycle_id": cycle["id"], "task": TASK, "database": str(database),
                 "cards": str(cards), "created_at": stamp(), "steps": 0,
                 "seeded_child": False, "seeded_sources": False}
        atomic_json(state_path, state)
        return state
    finally:
        store.close()


def call_context(database, cards, cycle_id):
    store = research.Research(database, cards)
    try:
        cycle = store.rows("SELECT stage FROM demand_cycles WHERE id=?", (cycle_id,))[0]
        snapshot = agent_snapshot(store, cycle_id)
        running = [task for task in snapshot.get("tasks", []) if task.get("status") == "running"]
        task = running[0] if len(running) == 1 else {}
        return {"stage": cycle["stage"], "task_id": task.get("id"),
                "role": task.get("role"), "parent_task_id": task.get("parent_task_id")}
    finally:
        store.close()


def safe_call_record(context, client, schema, successful, seconds, error_type=None):
    execution = getattr(client, "last_execution", None) or {}
    events = getattr(client, "last_web_events", None) or []
    urls = set()
    for event in events:
        if event.get("url"):
            urls.add(event["url"])
        urls.update(row["url"] for row in event.get("results", []) if row.get("url"))
    return {**context, "at": stamp(), "ok": successful, "seconds": round(seconds, 2),
            "error_type": error_type, "schema_keys": sorted(schema.get("properties", {})),
            "execution": {key: execution[key] for key in ("model", "reasoning_effort", "web_search", "read_only") if key in execution},
            "native_web_events": len(events), "native_public_urls": len(urls)}


@contextmanager
def trace_models(state, database, cards, directory):
    """Observe real calls without changing prompts, schemas, clients or replies."""
    from iva_integration import demand_sources
    from iva_integration.demand_sources import ResearchCodex, ResearchAnalysisCodex
    originals = [(cls, cls.structured) for cls in (ResearchCodex, ResearchAnalysisCodex)]
    original_fetch = demand_sources.fetch_source

    def wrapper(original):
        def call(client, prompt, schema):
            context = call_context(database, cards, state["cycle_id"])
            started, successful, error_type = time.monotonic(), False, None
            try:
                result = original(client, prompt, schema)
                successful = True
                return result
            except BaseException as error:
                error_type = type(error).__name__
                raise
            finally:
                row = safe_call_record(context, client, schema, successful,
                                       time.monotonic() - started, error_type)
                with (directory / "model-calls.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                emit({"event": "model_call", **row}, directory)
        return call

    def fetch(url):
        context = call_context(database, cards, state["cycle_id"])
        started = time.monotonic()
        result = original_fetch(url)
        row = {**context, "at": stamp(), "ok": result.get("ok") is True,
               "http_status": result.get("http_status"), "content_hash": result.get("content_hash"),
               "text_chars": len(result.get("text") or ""), "error_category": result.get("error_category"),
               "seconds": round(time.monotonic() - started, 2)}
        with (directory / "source-fetches.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        emit({"event": "public_source_fetch", **row}, directory)
        return result

    with patch.object(originals[0][0], "structured", wrapper(originals[0][1])), \
         patch.object(originals[1][0], "structured", wrapper(originals[1][1])), \
         patch.object(demand_sources, "fetch_source", fetch):
        yield


def acceptance(snapshot, calls, result, read_tool_ok, fetches=()):
    tasks = snapshot.get("tasks", [])
    roots = [task for task in tasks if not task.get("parent_task_id")]
    successful = [call for call in calls if call.get("ok")]

    def exercised(task):
        own = [call for call in successful if call.get("task_id") == task.get("id")]
        return (task.get("status") == "done"
                and any(call.get("stage") == "discovery" and call.get("native_web_events", 0) > 0 for call in own)
                and any(row.get("task_id") == task.get("id") and row.get("ok") and row.get("content_hash")
                        and row.get("text_chars", 0) > 0 for row in fetches)
                and all(any(call.get("stage") == stage for call in own)
                        for stage in ("analyze", "agent_report", "agent_review")))

    roles = {role: any(task.get("role") == role and exercised(task) for task in roots) for role in sorted(ROLES)}
    children = [task for task in tasks if task.get("parent_task_id")]
    child_runs = [task["id"] for task in children if exercised(task) and task.get("source_ids")
                  and (task.get("payload") or {}).get("source_ids")
                  and (task.get("payload") or {}).get("trigger_quote")]
    criteria = {
        "real_planner": any(call.get("stage") == "agent_plan" for call in successful),
        "four_distinct_root_roles": len(roots) == 4 and {task.get("role") for task in roots} == ROLES,
        "four_root_roles_actually_searched_fetched_analyzed_reported_and_reviewed": all(roles.values()),
        "real_synthesis": any(call.get("stage") == "agent_synthesis" for call in successful),
        "terminal_success": bool(result and result.get("ok") and not result.get("has_more")),
        "read_tool_status_and_report": read_tool_ok,
    }
    return {"passed": all(criteria.values()), "criteria": criteria, "roles": roles,
            "model_requested_children": len(children), "completed_live_children": child_runs,
            "child_path_exercised": bool(child_runs),
            "child_origin": "actual model output; diagnostic asked for deeper checks only on a real gap",
            "missing_coverage": [] if child_runs else ["No completed evidence-grounded child search, analysis, report and review was observed; recursive execution is not established by this run."],
            "limitations": "Successful execution is not a confirmed market opportunity, paid demand or independent customer count."}


def report(state, result, database, cards, directory):
    store = research.Research(database, cards)
    try:
        snapshot = agent_snapshot(store, state["cycle_id"])
        coverage = store.coverage(state["cycle_id"])
        events = store.rows("SELECT stage,event,detail,created FROM demand_events WHERE cycle_id=? ORDER BY id", (state["cycle_id"],))
        for event in events:
            event["detail"] = json.loads(event["detail"])
    finally:
        store.close()
    readbacks = {action: research.execute({"action": action, **({"id": state["cycle_id"]} if action in ("report", "sources") else {}), "limit": 20},
                                         db_path=database, cards_dir=cards)
                 for action in ("status", "report", "sources", "hypotheses")}
    trace = directory / "model-calls.jsonl"
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()] if trace.exists() else []
    trace = directory / "source-fetches.jsonl"
    fetches = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()] if trace.exists() else []
    value = {"at": stamp(), "state": state, "last_result": result, "agents": snapshot,
             "coverage": coverage, "model_calls": calls, "source_fetches": fetches, "events": events,
             "read_tool": readbacks,
             "acceptance": acceptance(snapshot, calls, result, all(value.get("ok") for value in readbacks.values()), fetches),
             "writes": "Verification research database/cards/vault and shared coordinator lease only; no Telegram, owner memory, finance or outbound actions."}
    atomic_json(directory / "final-report.json", value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=int, default=1800)
    args = parser.parse_args()
    if not 90 <= args.max_seconds <= 1800:
        parser.error("max-seconds must be between 90 and 1800")
    if os.name != "posix" or ROOT != Path("/opt/iva"):
        parser.error("Run only from /opt/iva on the VPS")
    database, cards, state_path, vault = paths()
    with deployment_read_lock(), exclusive(OUTPUT):
        from iva_integration.run_pilot import env as server_env
        from iva_integration import research_priority
        os.environ.update(server_env())
        helper = Path("/usr/local/bin/iva-codex")
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise RuntimeError("vpn_codex_helper_unavailable")
        os.environ["FINANCE_CODEX_EXE"] = str(helper)
        state = prepare(database, cards, state_path)
        emit({"event": "diagnostic_resumed" if state["steps"] else "diagnostic_created",
              "cycle_id": state["cycle_id"], "seeded_child": False})
        result = None
        with patch.object(research_priority, "STATUS", OUTPUT / "priority-status.json"), \
             research_lease(state, OUTPUT), trace_models(state, database, cards, OUTPUT):
            started = time.monotonic()
            while time.monotonic() - started < args.max_seconds - 80:
                result = research.run_step(state["job_id"], state["task"], db_path=database,
                                           cards_dir=cards, cycle_id=state["cycle_id"])
                state["steps"] += 1
                state["last_step_at"] = stamp()
                atomic_json(state_path, state)
                report(state, result, database, cards, OUTPUT)
                emit({"event": "step_finished", "step": state["steps"], "stage": result.get("stage"),
                      "status": result.get("status"), "has_more": result.get("has_more"),
                      "error_category": result.get("error_category")})
                if not result.get("has_more"):
                    break
                delay = result.get("retry_after_seconds", 1 if result.get("ok") else 20)
                delay = min(60, max(1, delay)) if type(delay) in (int, float) else 20
                remaining = args.max_seconds - 80 - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(min(delay, remaining))
            final = report(state, result, database, cards, OUTPUT)
            if result and not result.get("has_more"):
                from iva_integration.demand_memory import sync_cards
                memory = sync_cards(cards, vault)
                atomic_json(OUTPUT / "memory-status.json", {"at": stamp(), "cycle_id": state["cycle_id"], **memory})
                final = report(state, result, database, cards, OUTPUT)
                final["isolated_memory_export"] = memory
                final["memory_write_exercised"] = bool(memory.get("created") or memory.get("updated"))
                if final["memory_write_exercised"]:
                    repeated = sync_cards(cards, vault)
                    final["isolated_memory_replay"] = repeated
                    memory_ok = bool(memory.get("ok") and repeated.get("ok") and not repeated.get("created")
                                     and not repeated.get("updated") and not repeated.get("has_more"))
                    final["acceptance"]["criteria"]["isolated_memory_write_and_idempotent_replay"] = memory_ok
                    final["acceptance"]["passed"] = all(final["acceptance"]["criteria"].values())
                else:
                    final["acceptance"]["missing_coverage"].append("No new hypothesis card was written to the isolated vault; real memory writing is not established by this run.")
                atomic_json(OUTPUT / "final-report.json", final)
            emit({"event": "diagnostic_finished", "passed": final["acceptance"]["passed"],
                  "child_path_exercised": final["acceptance"]["child_path_exercised"],
                  "report": str(OUTPUT / "final-report.json")})
            return 0 if final["acceptance"]["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"event": "diagnostic_error", "error_type": type(error).__name__,
                          "category": str(error) if str(error).replace("_", "").isalnum() else "diagnostic_failed"}), flush=True)
        raise SystemExit(1)
