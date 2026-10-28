import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock

from deploy import verify_demand_agents as diagnostic


class AgentAcceptance(unittest.TestCase):
    def fetches(self, snapshot):
        return [{"task_id": task["id"], "ok": True, "content_hash": "test-hash", "text_chars": 1200}
                for task in snapshot["tasks"]]

    def fixture(self, child=False):
        tasks = [{"id": role, "role": role, "status": "done", "parent_task_id": None}
                 for role in sorted(diagnostic.ROLES)]
        if child:
            tasks.append({"id": "child", "role": "alternatives", "status": "done",
                          "parent_task_id": "alternatives", "source_ids": ["real-read-source"],
                          "payload": {"source_ids": ["real-parent-source"], "trigger_quote": "A real parent source excerpt."}})
        calls = [{"stage": stage, "ok": True} for stage in ("agent_plan", "agent_synthesis")]
        for task in tasks:
            calls += [{"task_id": task["id"], "stage": "discovery", "native_web_events": 2, "ok": True},
                      *[{"task_id": task["id"], "stage": stage, "ok": True}
                        for stage in ("analyze", "agent_report", "agent_review")]]
        return {"tasks": tasks}, calls

    def test_main_success_does_not_claim_unexercised_child(self):
        snapshot, calls = self.fixture()
        result = diagnostic.acceptance(snapshot, calls, {"ok": True, "has_more": False}, True, self.fetches(snapshot))
        self.assertTrue(result["passed"])
        self.assertFalse(result["child_path_exercised"])
        self.assertTrue(result["missing_coverage"])

    def test_child_requires_actual_search_and_analysis_after_grounded_assignment(self):
        snapshot, calls = self.fixture(child=True)
        result = diagnostic.acceptance(snapshot, calls, {"ok": True, "has_more": False}, True, self.fetches(snapshot))
        self.assertTrue(result["child_path_exercised"])
        without_child_calls = [row for row in calls if row.get("task_id") != "child"]
        result = diagnostic.acceptance(snapshot, without_child_calls, {"ok": True}, True, self.fetches(snapshot))
        self.assertFalse(result["child_path_exercised"])
        snapshot["tasks"][-1]["source_ids"] = []
        self.assertFalse(diagnostic.acceptance(snapshot, calls, {"ok": True}, True, self.fetches(snapshot))["child_path_exercised"])

    def test_stored_done_without_live_calls_never_passes(self):
        snapshot, calls = self.fixture()
        self.assertFalse(diagnostic.acceptance(snapshot, [], {"ok": True}, True, self.fetches(snapshot))["passed"])
        calls = [row for row in calls if row.get("task_id") != "critic"]
        self.assertFalse(diagnostic.acceptance(snapshot, calls, {"ok": True}, True, self.fetches(snapshot))["passed"])

    def test_model_search_and_analysis_without_real_source_reads_do_not_pass(self):
        snapshot, calls = self.fixture()
        self.assertFalse(diagnostic.acceptance(snapshot, calls, {"ok": True}, True)["passed"])
        failed = [{**row, "ok": False} for row in self.fetches(snapshot)]
        self.assertFalse(diagnostic.acceptance(snapshot, calls, {"ok": True}, True, failed)["passed"])

    def test_partial_or_broken_readback_is_not_success(self):
        snapshot, calls = self.fixture()
        self.assertFalse(diagnostic.acceptance(snapshot, calls, {"ok": True, "has_more": True}, True, self.fetches(snapshot))["passed"])
        self.assertFalse(diagnostic.acceptance(snapshot, calls, {"ok": True}, False, self.fetches(snapshot))["passed"])

    def test_record_omits_prompts_response_credentials_and_query_content(self):
        class Client:
            last_execution = {"model": "model", "web_search": "live", "auth": "SECRET"}
            last_web_events = [{"query": "PRIVATE", "results": [{"url": "https://example.org/a", "snippet": "HIDDEN"}]}]
        value = diagnostic.safe_call_record({"stage": "discovery"}, Client(), {"properties": {"candidates": {}}}, True, 1.23)
        for secret in ("SECRET", "PRIVATE", "HIDDEN", "https://example.org/a"):
            self.assertNotIn(secret, json.dumps(value))
        self.assertEqual(value["native_public_urls"], 1)

    def test_paths_reject_wrong_directory_and_symlinked_output(self):
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "release"
            release.mkdir()
            values = diagnostic.paths(release, release / "verification")
            self.assertTrue(all(path.parent == release / "verification" for path in values))
            with self.assertRaises(ValueError):
                diagnostic.paths(release, Path(temp) / "live")
            outside = Path(temp) / "outside"
            outside.mkdir()
            try:
                (release / "verification" / "cards").symlink_to(outside, target_is_directory=True)
            except OSError:
                return
            with self.assertRaises(ValueError):
                diagnostic.paths(release, release / "verification")

    def test_real_trace_wrapper_preserves_arguments_and_does_not_duplicate_calls(self):
        from iva_integration.demand_sources import ResearchCodex, ResearchAnalysisCodex
        captured = []
        response = {"opaque": "same result"}
        schema = {"properties": {"test": {}}}
        def original(client, prompt, supplied_schema):
            captured.append((prompt, supplied_schema))
            return response
        context = {"stage": "agent_plan", "task_id": None, "role": None, "parent_task_id": None}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(ResearchCodex, "structured", original), \
             patch.object(ResearchAnalysisCodex, "structured", original), \
             patch.object(diagnostic, "call_context", return_value=context), \
             patch.object(diagnostic, "emit"):
            directory = Path(temp)
            with diagnostic.trace_models({"cycle_id": "isolated"}, directory / "db", directory / "cards", directory):
                for cls in (ResearchCodex, ResearchAnalysisCodex):
                    client = object.__new__(cls)
                    self.assertIs(client.structured("unchanged prompt", schema), response)
            self.assertIs(ResearchCodex.structured, original)
            rows = [json.loads(row) for row in (directory / "model-calls.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["ok"] for row in rows))
            self.assertEqual(captured, [("unchanged prompt", schema)] * 2)

    def test_deployment_lock_is_shared_and_prevents_any_work_during_release(self):
        flock = Mock()
        fake = SimpleNamespace(flock=flock, LOCK_SH=1, LOCK_NB=4, LOCK_UN=8)
        with tempfile.TemporaryDirectory() as temp, patch.dict("sys.modules", {"fcntl": fake}):
            path = Path(temp) / "deploy.lock"
            path.write_bytes(b"")
            with diagnostic.deployment_read_lock(path):
                self.assertEqual(flock.call_args.args[1], 5)
            self.assertEqual(flock.call_args.args[1], 8)
            flock.reset_mock()
            flock.side_effect = BlockingIOError()
            with self.assertRaisesRegex(RuntimeError, "deployment_in_progress"):
                with diagnostic.deployment_read_lock(path):
                    self.fail("Diagnostic must not start during deployment")


if __name__ == "__main__":
    unittest.main()
