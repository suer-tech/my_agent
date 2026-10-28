"""Read-only production snapshot; maintenance is exercised on private copies only."""
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import traceback
from unittest.mock import patch


ROOT = Path("/opt/lead-flow")
SOURCE = ROOT / "data/research.sqlite3"


def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def tables(path):
    with closing(ro(path)) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result = {}
        for name in names:
            if not name.replace("_", "").isalnum():
                raise ValueError("unexpected_table_name")
            rows = sorted(json.dumps(list(row), ensure_ascii=False, sort_keys=True) for row in db.execute('SELECT * FROM "'+name+'"'))
            result[name] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest()}
        return result


def restore_member(archive, name, dest):
    with tarfile.open(archive) as handle:
        member = handle.getmember(name)
        if not member.isfile():
            raise ValueError("invalid_snapshot_member")
        dest.write_bytes(handle.extractfile(member).read())
    os.chmod(dest, 0o600)
    with closing(ro(dest)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("restored_integrity_failed")


def main():
    if os.geteuid() == 0:
        raise RuntimeError("Use the iva service account for this isolated probe")
    sys.path.insert(0, str(ROOT))
    from maintenance import maintain
    from worker.store import Store
    import scheduled
    from worker.engine import load_config
    folder = Path(tempfile.mkdtemp(prefix="lead-flow-maintenance-acceptance-"))
    os.chmod(folder, 0o700)
    clock = datetime.now(timezone.utc)
    report = {"started_at": clock.isoformat(), "source": str(SOURCE), "workspace": str(folder),
              "production_database_writes": False, "production_maintenance_run": False,
              "models_called": 0, "smtp_calls": 0, "checks": [],
              "source_hashes": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                for name in ("maintenance.py", "scheduled.py", "worker/store.py")}}
    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)
    try:
        original_before = tables(SOURCE)
        report["production_before"] = original_before
        copy = folder / "research.sqlite3"
        with closing(ro(SOURCE)) as original, closing(sqlite3.connect(copy)) as copied:
            original.backup(copied)
        check("online_copy_matches_production_tables", tables(copy) == original_before)
        store = Store(copy)
        old = (clock - timedelta(days=30)).isoformat(timespec="seconds")
        test_states = ("completed", "completed_with_gaps", "running", "failed", "waiting_resources", "pending_publish")
        for state in test_states:
            ident = "acceptance-" + state
            store.db.execute("INSERT INTO runs(id,campaign,slot,config_hash,state,created,updated) VALUES(?,?,?,?,?,?,?)",
                (ident, "acceptance", state, "synthetic-hash", state, old, old))
            source = {"text": "Exact retained test quote in original source body", "url": "https://synthetic.invalid/"+state,
                "title": "Synthetic source", "content_hash": "synthetic-content-hash"}
            store.db.execute("INSERT INTO pages VALUES(?,?,?)", (ident,source["url"],json.dumps(source)))
            store.db.execute("INSERT INTO candidates(run_id,domain,data,stage,assessment,result,lead_ack) VALUES(?,?,?,?,?,?,?)",
                (ident,state+".invalid","{}","preview",'{"quote":"Exact retained test quote"}',
                 '{"evidence":"Exact retained test quote","draft":"Synthetic draft"}','{"lead_id":123}'))
            store.db.execute("INSERT INTO queries(run_id,position,query,state,attempts) VALUES(?,?,?,?,?)",
                (ident,0,"Synthetic search checkpoint","done",2))
        store.db.commit();store.close()
        expected = tables(copy)
        with closing(ro(copy)) as db:
            expected_compacted = sum(bool(json.loads(row[0]).get("text")) for row in db.execute(
                "SELECT p.data FROM pages p JOIN runs r ON r.id=p.run_id WHERE r.state IN ('completed','completed_with_gaps') AND r.updated<?",
                ((clock-timedelta(days=14)).isoformat(timespec="seconds"),)))
        backup = maintain(copy, [], folder/"backups", clock=clock, reserve=0)
        report["copy_maintenance"] = backup
        check("terminal_source_bodies_compacted_only", backup["source_bodies_compacted"] == expected_compacted)
        current = tables(copy)
        check("all_knowledge_and_checkpoint_tables_unchanged", all(current[name] == value for name,value in expected.items() if name != "pages"))
        with closing(ro(copy)) as db:
            for state in test_states:
                value = json.loads(db.execute("SELECT data FROM pages WHERE run_id=?", ("acceptance-"+state,)).fetchone()[0])
                check("body_policy_"+state, value["text"] == ("" if state in {"completed","completed_with_gaps"}
                    else "Exact retained test quote in original source body"))
                check("source_metadata_retained_"+state, value["title"] == "Synthetic source" and value["content_hash"] == "synthetic-content-hash")
        restored = folder/"restored.sqlite3"
        archive = folder/"backups"/backup["backup"]
        restore_member(archive,"0-research.sqlite3",restored)
        check("backup_restore_equals_pre_retention_snapshot", tables(restored) == expected)
        check("archive_private_permissions", archive.stat().st_mode & 0o077 == 0)
        report["restored_database"] = str(restored)
        try:
            maintain(copy, [], folder/"backups", clock=clock, reserve=0)
            duplicate_refused = False
        except RuntimeError as exc:
            duplicate_refused = str(exc) == "backup_timestamp_exists"
        check("existing_backup_never_overwritten", duplicate_refused)
        before_guard = tables(copy)
        locked = Store(copy)
        try:
            with locked.exclusive():
                try:
                    maintain(copy, [], folder/"locked-backups", clock=clock, reserve=0)
                    lock_refused = False
                except RuntimeError as exc:
                    lock_refused = str(exc) == "worker_already_running"
        finally:
            locked.close()
        check("active_worker_lock_defers_maintenance", lock_refused and tables(copy) == before_guard)
        try:
            maintain(copy, [], folder/"space-guard", clock=clock, reserve=2**70)
            low_disk_refused = False
        except RuntimeError as exc:
            low_disk_refused = str(exc) == "insufficient_backup_space"
        check("space_guard_preserves_checkpoint", low_disk_refused and tables(copy) == before_guard)

        # Keep the writer connection open: the committed row remains in WAL.
        mail = folder/"mail-dispatch.sqlite3"
        with closing(sqlite3.connect(mail)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE attempts(id TEXT PRIMARY KEY,state TEXT)")
            writer.execute("INSERT INTO attempts VALUES('synthetic-attempt','uncertain')")
            writer.commit()
            check("wal_fixture_has_committed_sidecar", Path(str(mail)+"-wal").stat().st_size > 0)
            missing = folder/"missing-research.sqlite3"
            mail_result = maintain(missing,[mail],folder/"mail-backups",clock=clock,reserve=0)
            mail_restored = folder/"restored-mail.sqlite3"
            restore_member(folder/"mail-backups"/mail_result["backup"],"0-mail-dispatch.sqlite3",mail_restored)
            check("mail_only_backup_reads_wal_without_creating_research", not missing.exists() and mail_result["databases"] == 1
                and tables(mail_restored) == tables(mail))

        enabled = {"enabled":True}
        day = datetime(2026,10,26,9)
        check("disabled_schedule_never_admits_models", scheduled.due({"enabled":False},day,[{"state":"running","slot":"2026-10-25"}]) == (False,"disabled"))
        check("one_new_cycle_per_day", scheduled.due(enabled,day,[]) == (True,"new_daily_cycle")
            and scheduled.due(enabled,day,[{"state":"completed","slot":"2026-10-26"}])[0] is False)
        for state in ("running","failed","waiting_resources","pending_publish"):
            check("scheduled_resumes_"+state, scheduled.due(enabled,day,[{"state":state,"slot":"2026-10-25"}]) == (True,"resume_checkpoint"))
        check("admission_window_09_to_22", scheduled.due(enabled,day.replace(hour=8,minute=59),[])[0] is False
            and scheduled.due(enabled,day.replace(hour=21,minute=59),[])[0] is True
            and scheduled.due(enabled,day.replace(hour=22),[])[0] is False)
        config = load_config("/etc/lead-flow/research.json")
        report["live_config"] = {key:config.get(key) for key in ("enabled","mode","campaign","database","timezone")}
        output = io.StringIO()
        with patch.object(scheduled,"load_config",return_value={**config,"enabled":False}), \
             patch.object(scheduled.os,"execv",side_effect=AssertionError("disabled schedule tried exec")),redirect_stdout(output):
            exit_code = scheduled.main()
        disabled_result = json.loads(output.getvalue())
        check("scheduled_main_disabled_does_not_exec_worker", exit_code == 0 and disabled_result["state"] == "disabled" and disabled_result["models_called"] == 0)
        original_after = tables(SOURCE)
        report["production_after"] = original_after
        check("production_source_tables_unchanged", original_after == original_before)
        report["ok"] = True
    except Exception as exc:
        report.update(ok=False,error_type=type(exc).__name__,error=str(exc)[:1000],traceback=traceback.format_exc()[-4000:])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_path = folder/"report.json"
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    os.chmod(report_path,0o600)
    print(json.dumps({"ok":report["ok"],"passed":sum(item["passed"] for item in report["checks"]),
                      "checks":len(report["checks"]),"report":str(report_path),"production_database_writes":False}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
