from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import demand_questions as q


def proposal(key="adoption", priority="high", geography="domestic"):
    return {"question_key": key, "question": "Кто использует ExampleCRM для сверки заказов?",
            "query": "ExampleCRM отзывы сверка заказов", "reason": "Проверить существующую альтернативу перед разработкой.",
            "priority": priority, "kind": "competitor_reviews", "geography": geography,
            "region": "Россия" if geography == "domestic" else "Европа", "subject": "ExampleCRM",
            "pass_condition": "Есть конкретные рассказы о применении для сверки заказов.",
            "fail_condition": "Проверенные рассказы показывают отсутствие этой возможности.", "source_ids": ["s1"]}


class Questions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="demand-questions-tests-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "research.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(lambda: self.db.close())
        self.db.executescript("""
            CREATE TABLE demand_cycles(id TEXT,job_id TEXT,track TEXT);
            CREATE TABLE demand_hypotheses(id TEXT,job_id TEXT,track TEXT);
            CREATE TABLE demand_sources(id TEXT,url TEXT,text TEXT);
            CREATE TABLE demand_candidates(cycle_id TEXT,source_id TEXT,state TEXT);
            INSERT INTO demand_cycles VALUES('c1','job','software'),('c2','job','software'),('foreign','other','software');
            INSERT INTO demand_hypotheses VALUES('h1','job','software'),('h2','other','software');
            INSERT INTO demand_sources VALUES('s1','https://example.com/real','We use ExampleCRM for manual order reconciliation.'),
                ('s2','https://forum.example.net/post','ExampleCRM lacks the required feature for recurring reconciliation.'),
                ('s3','https://example.com/unread','ExampleCRM unseen source');
            INSERT INTO demand_candidates VALUES('c1','s1','read'),('c1','s3','pending'),('c2','s2','read');
        """)
        q.ensure(self.db)
        self.db.commit()

    def add(self, items=None, parent=None, cycle="c1", allowed=None):
        return q.enqueue(self.db, "job", "software", cycle, "h1", items or [proposal()],
                         allowed if allowed is not None else {"s1"}, parent)

    def test_grounded_question_persists_with_original_criteria_and_readonly_pick(self):
        identity = self.add()["queued"][0]
        before = self.db.total_changes
        picked = q.pick(self.db, "job", "software")
        self.assertEqual(self.db.total_changes, before)
        self.assertEqual(picked["id"], identity)
        self.assertEqual(picked["payload"]["pass_condition"], proposal()["pass_condition"])
        self.db.commit()
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.assertEqual(q.pick(self.db, "job")["id"], identity)

    def test_invented_unread_or_unrelated_sources_do_not_ground_questions(self):
        for identity in ("invented", "s3", "s2"):
            item = proposal(); item["source_ids"] = [identity]
            result = self.add([item], allowed={identity: {"text": "ExampleCRM fabricated"}})
            self.assertEqual(result["rejected"][0]["reason"], "question_sources_not_read_in_cycle")
        self.assertEqual(q.summary(self.db)["counts"], {})

    def test_named_subject_and_url_must_be_in_actual_read_sources(self):
        item = proposal(); item["subject"] = "InventedProgram"
        self.assertEqual(self.add([item])["rejected"][0]["reason"], "question_subject_not_in_sources")
        item = proposal(); item["query"] += " https://invented.example/product"
        self.assertEqual(self.add([item])["rejected"][0]["reason"], "question_url_not_in_sources")
        self.db.execute("UPDATE demand_sources SET text='Our program uses C# to reconcile orders.' WHERE id='s1'")
        item = proposal(); item["subject"] = "C++"
        self.assertEqual(self.add([item])["rejected"][0]["reason"], "question_subject_not_in_sources")

    def test_duplicate_key_scope_preserves_predeclared_criteria(self):
        identity = self.add()["queued"][0]
        item = proposal(); item["question"] = "Переформулированный вопрос"; item["pass_condition"] = "Изменённый критерий"
        result = self.add([item])
        self.assertEqual(result["existing"], [identity])
        self.assertEqual(q.pick(self.db, "job")["payload"]["pass_condition"], proposal()["pass_condition"])
        overseas = self.add([proposal(geography="international")])
        self.assertEqual(len(overseas["queued"]), 1)

    def test_parent_scope_and_hypothesis_scope_are_enforced(self):
        identity = self.add()["queued"][0]
        with self.assertRaisesRegex(ValueError, "parent_scope_mismatch"):
            q.enqueue(self.db, "other", "software", "foreign", None, [proposal()], {"s1"}, identity)
        with self.assertRaisesRegex(ValueError, "hypothesis_scope_mismatch"):
            q.enqueue(self.db, "job", "software", "c1", "h2", [proposal()], {"s1"})

    def test_depth_limit_is_visible_and_never_auto_selected(self):
        parent = None
        for index in range(4):
            result = self.add([proposal("depth-" + str(index))], parent)
            parent = (result["queued"] + result["deferred"])[0]
        rows = q.summary(self.db)["questions"]
        deep = next(row for row in rows if row["id"] == parent)
        self.assertEqual(deep["depth"], 4)
        self.assertEqual(deep["state"], "needs_owner")
        self.assertNotEqual(q.pick(self.db, "job")["id"], parent)

    def test_priority_budget_defers_surplus_and_replenishes_after_completion(self):
        with patch.object(q, "MAX_OPEN_PER_THEME", 2):
            normal = self.add([proposal("normal-a", "normal"), proposal("normal-b", "normal")])["queued"]
            critical = self.add([proposal("urgent", "critical")])["queued"][0]
            counts = q.summary(self.db)["counts"]
            self.assertEqual(counts, {"deferred": 1, "pending": 2})
            self.assertEqual(q.pick(self.db, "job")["id"], critical)
            q.start(self.db, critical, "c2")
            q.complete(self.db, critical, "inconclusive", "No decisive observation in this bounded check.", [])
            self.assertEqual(q.summary(self.db)["counts"], {"parked": 1, "pending": 2})
            self.assertIn(q.pick(self.db, "job")["id"], normal)

    def test_start_is_idempotent_only_for_assigned_cycle(self):
        identity = self.add()["queued"][0]
        first = q.start(self.db, identity, "c1")
        self.assertEqual(q.start(self.db, identity, "c1"), first)
        with self.assertRaisesRegex(ValueError, "question_not_pending"):
            q.start(self.db, identity, "c2")

    def test_completion_requires_evidence_from_actual_assigned_cycle(self):
        identity = self.add()["queued"][0]
        q.start(self.db, identity, "c2")
        with self.assertRaisesRegex(ValueError, "question_result_sources_not_read"):
            q.complete(self.db, identity, "supported", "Prior unrelated evidence is insufficient.", ["s1"])
        with self.assertRaisesRegex(ValueError, "question_result_sources_not_read"):
            q.complete(self.db, identity, "supported", "A plan is not a result.", [])
        result = q.complete(self.db, identity, "contradicted", "Read evidence states the feature is absent.", ["s2"])
        self.assertEqual(result["state"], "contradicted")
        self.assertEqual(result["payload"]["fail_condition"], proposal()["fail_condition"])
        self.assertEqual(q.complete(self.db, identity, "contradicted", "Read evidence states the feature is absent.", ["s2"]), result)

    def test_inconclusive_is_parked_and_dedup_does_not_requeue(self):
        identity = self.add()["queued"][0]
        q.start(self.db, identity, "c2")
        q.complete(self.db, identity, "inconclusive", "Access was incomplete; demand not disproved.", [])
        self.assertIsNone(q.pick(self.db, "job"))
        self.assertEqual(self.add()["existing"], [identity])
        self.assertIsNone(q.pick(self.db, "job"))

    def test_caller_transaction_rollback_removes_queue_write(self):
        self.add()
        self.db.rollback()
        self.assertEqual(q.summary(self.db)["counts"], {})


if __name__ == "__main__":
    unittest.main()
