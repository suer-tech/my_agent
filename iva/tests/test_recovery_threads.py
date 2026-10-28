import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from iva_integration.recovery_threads import collect


class ThreadsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "repair").mkdir()
        self.path = self.root / "repair" / "repair.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE jobs(id TEXT PRIMARY KEY,campaign TEXT,incident TEXT,code TEXT,
                state TEXT,evidence TEXT,created REAL,updated REAL,attempts INTEGER,next_at REAL,result TEXT);
            CREATE TABLE observations(campaign TEXT,incident TEXT,code TEXT,samples TEXT,
                last_seen REAL,active INTEGER,last_job TEXT,last_dispatch REAL);
        """)
        self.ident = "a" * 32
        self.result = {"installed": True, "tests_passed": True, "installed_at": 110,
                       "verified_at": 130, "reason": "live_inbox_check_passed",
                       "root_cause": "SECRET body https://private.invalid/?token=SECRET"}
        self.inbox = {"state": "ok", "checked_at": 120, "last_success_at": 121,
                      "finished_at": 122, "folders": ["inbox", "requests", "hidden"],
                      "checked_folders": ["inbox", "requests", "hidden"]}
        self.previous = collect(self.root, None, 100)["state"]
        self.add_job()
        self.write_inbox()

    def add_job(self, ident=None, campaign="extension", state="verified", result=None):
        ident = ident or self.ident
        self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ident, campaign, "inbox:messages", "messages", state, "{}", 90, 130,
             1, 130, json.dumps(result or self.result)))
        self.db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",
            (campaign, "inbox:messages", "messages", "[]", 130, 0, ident, 90))
        self.db.commit()

    def write_inbox(self):
        (self.root / "inbox-status.json").write_text(json.dumps(self.inbox), encoding="utf-8")

    def collected(self):
        return collect(self.root, self.previous, 150)

    def change_result(self, **changes):
        value = {**self.result, **changes}
        self.db.execute("UPDATE jobs SET result=?", (json.dumps(value),))
        self.db.commit()

    def test_baselines_old_completed_jobs_without_notifications(self):
        first = collect(self.root, None, 140)
        self.assertEqual(first["events"], [])
        self.assertEqual(collect(self.root, first["state"], 150)["events"], [])

    def test_preexisting_active_job_can_finish_after_baseline(self):
        self.db.execute("UPDATE jobs SET state='awaiting_live_verification'")
        self.db.commit()
        baseline = collect(self.root, None, 105)["state"]
        self.db.execute("UPDATE jobs SET state='verified'")
        self.db.commit()
        events = collect(self.root, baseline, 150)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "code_patch")
        self.assertTrue(events[0]["proof"]["all_folders_checked"])

    def test_stable_identity_deduplicates_across_polls_without_private_text(self):
        first, second = self.collected(), self.collected()
        self.assertEqual(first["events"][0]["id"], second["events"][0]["id"])
        self.assertEqual(first["state"], self.previous)
        self.assertNotIn("SECRET", json.dumps(first))
        self.assertNotIn("https", json.dumps(first))
        self.assertEqual(first["events"][0]["source"], "threads")

    def test_unverified_and_disappeared_jobs_are_not_recovery(self):
        for state in ("awaiting_live_verification", "resolved_without_repair", "review_required", "failed"):
            with self.subTest(state=state):
                self.db.execute("UPDATE jobs SET state=?", (state,))
                self.db.commit()
                self.assertEqual(self.collected()["events"], [])

    def test_tests_install_and_valid_fresh_timestamps_are_required(self):
        for changes in ({"tests_passed": False}, {"tests_passed": 1}, {"installed": False},
                        {"installed_at": None}, {"verified_at": 90}, {"verified_at": 151},
                        {"installed_at": float("nan")}, {"reason": "installed_waiting_for_reader_check"}):
            with self.subTest(changes=changes):
                self.change_result(**changes)
                self.assertEqual(self.collected()["events"], [])

    def test_stale_partial_or_failed_inbox_is_not_live_proof(self):
        original = dict(self.inbox)
        for changes in ({"checked_at": 109}, {"state": "error"},
                        {"folders": ["inbox", "requests"]}, {"checked_folders": ["inbox"]},
                        {"last_success_at": None}, {"finished_at": 160}, {"checked_at": True},
                        {"folders": ["inbox", "requests", {}]}):
            with self.subTest(changes=changes):
                self.inbox = {**original, **changes}
                self.write_inbox()
                self.assertEqual(self.collected()["events"], [])

    def test_recurrence_and_replaced_job_do_not_report_old_recovery(self):
        for active, last_job in ((1, self.ident), (0, "b" * 32)):
            with self.subTest(active=active, last_job=last_job):
                self.db.execute("UPDATE observations SET active=?,last_job=?", (active, last_job))
                self.db.commit()
                self.assertEqual(self.collected()["events"], [])

    def test_new_episode_gets_distinct_event_identity(self):
        old = self.collected()["events"][0]["id"]
        self.db.execute("DELETE FROM observations")
        self.db.commit()
        self.add_job(ident="b" * 32)
        events = self.collected()["events"]
        self.assertEqual(len(events), 1)
        self.assertNotEqual(events[0]["id"], old)

    def test_both_campaigns_are_supported(self):
        self.add_job(ident="b" * 32, campaign="development")
        self.assertEqual({e["campaign"] for e in self.collected()["events"]}, {"extension", "development"})

    def test_missing_and_corrupt_sources_fail_closed(self):
        inbox_path = self.root / "inbox-status.json"
        inbox_path.unlink()
        self.assertEqual(self.collected()["events"], [])
        inbox_path.write_text("{bad", encoding="utf-8")
        self.assertEqual(self.collected()["events"], [])
        self.write_inbox()
        self.db.execute("UPDATE jobs SET result='[]'")
        self.db.commit()
        self.assertEqual(self.collected()["events"], [])
        self.db.execute("DROP TABLE observations")
        self.db.commit()
        self.assertEqual(self.collected()["events"], [])

    def test_does_not_mutate_repair_database(self):
        before = self.path.read_bytes()
        self.assertEqual(len(self.collected()["events"]), 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_prior_state_rebaselines_without_backlog(self):
        for previous in ({}, {"version": 1, "baseline_at": 999}, {"version": 1, "baseline_at": float("nan")}):
            with self.subTest(previous=previous):
                result = collect(self.root, previous, 150)
                self.assertEqual(result["events"], [])
                self.assertEqual(result["state"]["baseline_at"], 150)

    def test_diagnostics_distinguish_no_repair_from_missing_proof_and_source(self):
        self.assertEqual(self.collected()['diagnostics'], {'threads': 'read_ok'})
        (self.root / 'inbox-status.json').unlink()
        self.assertEqual(self.collected()['diagnostics'], {'threads': 'proof_unavailable'})
        self.db.execute("UPDATE jobs SET state='pending'")
        self.db.commit()
        self.assertEqual(self.collected()['diagnostics'], {'threads': 'no_verified_repair'})
        self.db.execute('DROP TABLE jobs')
        self.db.commit()
        self.assertEqual(self.collected()['diagnostics'], {'threads': 'source_unavailable'})


if __name__ == "__main__":
    unittest.main()
