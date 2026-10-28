"""Real public-research diagnostic on an isolated copy of the earlier live fixture.

No Telegram, scheduler, financial database, or owner-vault writes. This script
does not install code. It acquires the coordinator's research lease, which pauses
background browsers until release. Resume uses the same stored cycle ID.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iva_integration import demand_questions as questions
from iva_integration import demand_research as research

RELEASE = ROOT / "data/demand-release-20261025"
SOURCE = RELEASE / "verification-v2/research.sqlite3"
OUTPUT = RELEASE / "verification-v3"
SUBJECTS = ("МойСклад", "Мой Склад", "Odoo", "Бизнес.Ру", "RetailCRM")


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(value, directory):
    row = {"at": stamp(), **value}
    text = json.dumps(row, ensure_ascii=False, sort_keys=True)
    print(text, flush=True)
    with (directory / "progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(text + "\n")
        stream.flush()


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def exclusive(directory):
    import fcntl
    with (directory / ".verification.lock").open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("diagnostic_already_running") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def research_lease(state, directory, timeout=120):
    """Keep the real research and its lease in the same authenticated process."""
    from iva_integration import research_priority
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            result = research_priority.acquire(state['job_id'], state['cycle_id'])
            granted = bool(result.get('ok') and result.get('granted'))
            # Never log the response wholesale: it may contain lease_token.
            emit({'event': 'research_lease', 'status': result.get('status'),
                  'granted': granted, 'cycle_id': state['cycle_id']}, directory)
            if granted:
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(5, remaining))
        else:
            raise RuntimeError('diagnostic_research_lease_wait_timeout')
        yield
    finally:
        # This also cancels our pending lease after timeout/interrupt. Busy
        # leases belonging to another process never enter this client's _held.
        released = False
        for attempt in range(3):
            try:
                released = research_priority.release()
            except Exception:
                released = False
            if released:
                break
            if attempt < 2:
                time.sleep(2)
        emit({'event': 'research_lease_cleanup', 'released': bool(released),
              'cycle_id': state['cycle_id']}, directory)


def paths():
    # Intentionally no caller-supplied database/vault paths: the only writable
    # scope is this release's verification-v3. Reject links into another tree.
    for path in (RELEASE, SOURCE.parent, SOURCE, OUTPUT):
        if path.is_symlink():
            raise RuntimeError("diagnostic_path_must_not_be_symlink")
    if SOURCE.resolve().parent != RELEASE.resolve() / "verification-v2":
        raise RuntimeError("unexpected_seed_path")
    if OUTPUT.resolve() != RELEASE.resolve() / "verification-v3":
        raise RuntimeError("unexpected_output_path")
    if not SOURCE.is_file():
        raise RuntimeError("earlier_live_diagnostic_missing")
    OUTPUT.mkdir(mode=0o700, parents=False, exist_ok=True)
    return OUTPUT / "research.sqlite3", OUTPUT / "cards", OUTPUT / "state.json"


def copy_seed(destination):
    if destination.exists():
        return
    with closing(sqlite3.connect(SOURCE.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
            copied.commit()
    os.chmod(destination, 0o600)


def seed_question(database, cards):
    store = research.Research(database, cards)
    try:
        # The old fixture's actual read source supplies only a name/provenance,
        # never a fabricated finding about adoption, limitations, or willingness
        # to pay. Prefer the explicitly requested product when it is literal.
        sources = store.rows("""SELECT s.*,c.cycle_id,y.job_id,y.track
            FROM demand_sources s JOIN demand_candidates c ON c.source_id=s.id
            JOIN demand_cycles y ON y.id=c.cycle_id
            WHERE c.state='read' AND y.track='software' ORDER BY s.fetched_at DESC""")
        selected = None
        for subject in SUBJECTS:
            for source in sources:
                position = source["text"].casefold().find(subject.casefold())
                if position >= 0:
                    selected = (source, source["text"][position:position + len(subject)])
                    break
            if selected:
                break
        if selected is None:
            raise RuntimeError("no_verified_named_program_in_seed_sources")
        source, subject = selected
        question = {
            "question_key": "verified-program-order-reconciliation-limits-and-reviews",
            "question": f"Какие ограничения {subject} при импорте и сверке заказов подтверждаются документацией и реальными отзывами пользователей?",
            "query": f'"{subject}" импорт заказов сверка остатков ограничения отзывы пользователей интеграция',
            "reason": "Программа упомянута в ранее действительно прочитанном источнике. Следует проверить, решает ли существующая альтернатива предполагаемую задачу; недостатки и внедрение заранее не утверждаются.",
            "priority": "critical", "kind": "competitor_reviews", "geography": "domestic",
            "region": "Россия; зарубежные примеры учитывать отдельно", "subject": subject,
            "pass_condition": "Прочитанные документация и конкретные пользовательские рассказы согласованно описывают ограничение импорта или сверки, существенное для названного процесса.",
            "fail_condition": "Прочитанные документация и конкретные пользовательские рассказы показывают, что предполагаемое ограничение уже закрыто доступным способом; рекламное обещание само по себе недостаточно.",
            "source_ids": [source["id"]],
        }
        with store.db:
            queued = questions.enqueue(store.db, source["job_id"], "software", source["cycle_id"], None,
                                       [question], {source["id"]: source})
            identities = queued["queued"] + queued["existing"]
            if not identities:
                raise RuntimeError("grounded_seed_question_rejected")
            question_id = identities[0]
            # Only the COPIED historical slots/states are isolated from the new
            # current-hour run. Preserve this explicit fixture manipulation.
            archived = store.rows("SELECT id,slot,state,stage,sequence FROM demand_cycles WHERE job_id=?", (source["job_id"],))
            for row in archived:
                store.db.execute("UPDATE demand_cycles SET slot=?,state=?,stage='done' WHERE id=?",
                                 ("seed-old:" + row["id"] + ":" + row["slot"],
                                  "diagnostic_seed" if row["state"] == "running" or row["stage"] == "export_pending" else row["state"], row["id"]))
            # Production deliberately reserves odd-numbered cycles for queued
            # deepening. Select such a slot only in this copied fixture.
            latest = max(archived, key=lambda row: row["sequence"])
            if (latest["sequence"] + 1) % 2 == 0:
                store.db.execute("UPDATE demand_cycles SET sequence=sequence+1 WHERE id=?", (latest["id"],))
            store.event(source["cycle_id"], "diagnostic", "fixture_seed_only", {
                "source_id": source["id"], "question_id": question_id,
                "note": "Question deliberately seeded from prior real-read source; not an automatically discovered new fact.",
            })
        candidate = questions.pick(store.db, source["job_id"], "software")
        if not candidate or candidate["id"] != question_id:
            raise RuntimeError("another_seed_question_has_priority")
        original_tracks = research.TRACKS
        try:
            # Only initial cycle selection is pinned to software. All four
            # discovery batches, HTTP fetches and model analyses remain real.
            research.TRACKS = (("software", "Диагностическая проверка найденной программы"),)
            cycle = store.cycle(source["job_id"], "Диагностика уточняющей ветки: выполнить сохранённый вопрос по публичным источникам; не писать людям, не покупать и не публиковать.")
        finally:
            research.TRACKS = original_tracks
        plan = json.loads(cycle["plan"])
        if not plan.get("question") or plan["question"]["id"] != question_id:
            raise RuntimeError("seed_question_not_selected_by_real_pipeline")
        return {
            "version": 1, "seeded_fixture": True, "created_at": stamp(),
            "job_id": source["job_id"], "cycle_id": cycle["id"], "question_id": question_id,
            "task": cycle["task"], "steps": 0,
            "seed_source": {key: source[key] for key in ("id", "url", "title", "fetched_at", "published_at", "content_hash", "cycle_id")},
            "subject": subject, "seed_question": question, "archived_copy_cycles": archived,
            "fixture_adjustments": ["SQLite backup of verification-v2 only", "copied historical slots prefixed seed-old", "copied unfinished states detached as diagnostic_seed", "copied last sequence adjusted if needed to select odd deepening slot", "initial track selection pinned to software"],
            "source_database": str(SOURCE), "database": str(database), "cards": str(cards),
        }
    finally:
        store.close()


def save_report(state, result, database, cards, elapsed, terminal):
    store = research.Research(database, cards)
    try:
        events = store.rows("SELECT * FROM demand_events WHERE cycle_id=? ORDER BY id", (state["cycle_id"],))
        for event in events:
            event["detail"] = json.loads(event["detail"])
        frontier = questions.summary(store.db, state["job_id"], limit=100)
        report = {"seeded_fixture": True, "state": state, "last_result": result,
                  "terminal": terminal, "elapsed_seconds": round(elapsed, 2),
                  "coverage": store.coverage(state["cycle_id"]), "questions": frontier,
                  "writes": "Research fixtures in verification-v3 only; shared coordinator research lease/status; no Telegram, financial database, or owner vault writes",
                  "limitations": "This is a deliberately seeded branch over real searches and reads. It does not prove an automatically discovered business opportunity or willingness to pay."}
        atomic_json(OUTPUT / "events.json", events)
        atomic_json(OUTPUT / "questions.json", frontier)
        atomic_json(OUTPUT / "final-report.json", report)
        return report
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=int, default=1200)
    parser.add_argument("--check-memory", action="store_true", help="Export only to verification-v3/vault after a terminal cycle")
    args = parser.parse_args()
    if not 90 <= args.max_seconds <= 1200:
        parser.error("max-seconds must be between 90 and 1200")
    if os.name != "posix" or ROOT != Path("/opt/iva"):
        parser.error("Run this diagnostic only from /opt/iva on the VPS")
    database, cards, state_path = paths()
    with exclusive(OUTPUT):
        # Load the same private runtime configuration as server jobs, without
        # printing it. Codex must always use the dedicated VPN namespace helper.
        from iva_integration.run_pilot import env as server_env
        os.environ.update(server_env())
        helper = Path("/usr/local/bin/iva-codex")
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise RuntimeError("vpn_codex_helper_unavailable")
        os.environ["FINANCE_CODEX_EXE"] = str(helper)
        copy_seed(database)
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not state.get("seeded_fixture") or state.get("database") != str(database):
                raise RuntimeError("invalid_diagnostic_resume_state")
        else:
            state = seed_question(database, cards)
            atomic_json(state_path, state)
        emit({"event": "resumed" if state["steps"] else "seeded", "cycle_id": state["cycle_id"],
              "question_id": state["question_id"], "subject": state["subject"], "seeded_fixture": True}, OUTPUT)
        with research_lease(state, OUTPUT):
            started = time.monotonic()
            result = None
            terminal = False
            # Leave room for one bounded model call. run_step supplies real clients;
            # no fake CLI, synthetic HTTP response, notification dispatcher or tools.
            while time.monotonic() - started < args.max_seconds - 80:
                emit({"event": "step_started", "step": state["steps"] + 1, "cycle_id": state["cycle_id"]}, OUTPUT)
                result = research.run_step(state["job_id"], state["task"], db_path=database,
                                           cards_dir=cards, cycle_id=state["cycle_id"])
                state["steps"] += 1
                state["last_step_at"] = stamp()
                atomic_json(state_path, state)
                coverage = result.get("coverage", {})
                emit({"event": "step_finished", "step": state["steps"], "status": result.get("status"),
                      "stage": result.get("stage"), "has_more": result.get("has_more"),
                      "read": coverage.get("read"), "analyzed": coverage.get("analyzed"),
                      "search_batches_completed": coverage.get("search_batches_completed"),
                      "error_category": result.get("error_category")}, OUTPUT)
                terminal = not result.get("has_more", False)
                save_report(state, result, database, cards, time.monotonic() - started, terminal)
                if terminal:
                    break
                category = result.get("error_category")
                fallback = 60 if category == "shared_model_busy" else 20 if category == "model_timeout" else 2
                requested = result.get("retry_after_seconds", fallback)
                delay = max(2, float(requested)) if isinstance(requested, (int, float)) else fallback
                wake = min(time.monotonic() + delay, started + args.max_seconds - 80)
                if time.monotonic() < wake:
                    emit({"event": "yielding_between_steps", "delay_seconds": round(wake - time.monotonic(), 1),
                          "reason": category or "fairness"}, OUTPUT)
                while time.monotonic() < wake:
                    time.sleep(min(15, max(0, wake - time.monotonic())))
            report = save_report(state, result, database, cards, time.monotonic() - started, terminal)
            if terminal and args.check_memory and cards.exists():
                from iva_integration.demand_memory import sync_cards
                memory = sync_cards(cards, OUTPUT / "vault")
                report["isolated_memory_export"] = memory
                atomic_json(OUTPUT / "final-report.json", report)
            emit({"event": "terminal" if terminal else "checkpointed_at_time_limit", "cycle_id": state["cycle_id"],
                  "report": str(OUTPUT / "final-report.json"), "seeded_fixture": True}, OUTPUT)
            return 0 if terminal and result and result.get("ok") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"event": "diagnostic_error", "error_type": type(error).__name__,
                          "category": str(error) if str(error).replace("_", "").isalnum() else "diagnostic_failed"}), flush=True)
        raise SystemExit(1)
