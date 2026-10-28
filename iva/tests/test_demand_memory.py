"""Native-protocol integration tests; every write is under a temporary vault."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from iva_integration import demand_memory as memory


class DemandMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="demand-memory-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.vault = self.root / "vault"
        self.card = self.source / "abc123.md"
        self.card.write_text("# Хранение инструмента\n\nНеизвестна готовность платить.\n"
                             "Источник: https://example.com/original\n", encoding="utf-8")

    def sync(self):
        return memory.sync_cards(self.source, self.vault)

    def target(self):
        return next((self.vault / "cards/ideas/demand-research").glob("*.md"))

    def test_native_idea_schema_provenance_and_readonly_source(self):
        original = self.card.read_bytes()
        result = self.sync()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["created"], 1)
        output = self.target().read_text(encoding="utf-8")
        frontmatter = output.split("---\n", 2)[1]
        fields = dict(line.split(": ", 1) for line in frontmatter.splitlines())
        self.assertEqual(json.loads(fields["type"]), "idea")
        self.assertEqual(json.loads(fields["status"]), "draft")
        self.assertIn("description:", output)
        self.assertIn("tags:", output)
        self.assertIn("https://example.com/original", output)
        self.assertIn(hashlib.sha256(original).hexdigest(), output)
        self.assertIn("не факт о владельце", output)
        self.assertEqual(self.card.read_bytes(), original)
        self.assertFalse((self.vault / "cards/.write_card.lock").exists())

    def test_idempotent_export_does_not_rewrite_when_source_touched(self):
        self.assertTrue(self.sync()["ok"])
        output = self.target()
        before = output.stat().st_mtime_ns
        os.utime(self.card, (1900000000, 1900000000))
        result = self.sync()
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(output.stat().st_mtime_ns, before)

    def test_history_larger_than_batch_limit_progresses_without_loss(self):
        for index in range(4):
            (self.source / f"other-{index}.md").write_text(f"# Idea {index}\nEvidence", encoding="utf-8")
        with patch.object(memory, "MAX_CARDS", 2):
            results = [self.sync(), self.sync(), self.sync()]
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual([result["created"] for result in results], [2, 2, 1])
        self.assertEqual([result["pending"] for result in results], [3, 1, 0])
        self.assertEqual([result["has_more"] for result in results], [True, True, False])
        self.assertEqual(len(list((self.vault / "cards/ideas/demand-research").glob("*.md"))), 5)
        with patch.object(memory.subprocess, "run") as run:
            result = self.sync()
            run.assert_not_called()
        self.assertEqual(result["unchanged"], 5)
        self.assertFalse(result["has_more"])

    def test_byte_budget_is_a_resumable_batch_not_total_history_limit(self):
        self.card.write_text("# One\n" + "x" * 150, encoding="utf-8")
        (self.source / "second.md").write_text("# Two\n" + "y" * 150, encoding="utf-8")
        with patch.object(memory, "MAX_TOTAL_BYTES", 250):
            first, second = self.sync(), self.sync()
        self.assertTrue(first["ok"] and second["ok"], (first, second))
        self.assertEqual((first["created"], first["pending"], first["has_more"]), (1, 1, True))
        self.assertEqual((second["created"], second["pending"], second["has_more"]), (1, 0, False))

    def test_native_partial_failure_indexes_only_confirmed_prefix(self):
        for index in range(2):
            (self.source / f"other-{index}.md").write_text(f"# Idea {index}\nEvidence", encoding="utf-8")
        script = memory._EXPORT_SCRIPT.replace(
            "if (existingStat) result.updated++; else result.created++;",
            "if (existingStat) result.updated++; else result.created++; "
            "if (result.created === 2) throw Error('synthetic_interruption');")
        with patch.object(memory, "_EXPORT_SCRIPT", script):
            first = self.sync()
        self.assertFalse(first["ok"])
        self.assertEqual(first["pending"], 2)
        self.assertTrue(first["has_more"])
        second = self.sync()
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["created"], 1)
        self.assertEqual(second["unchanged"], 2)
        self.assertFalse(second["has_more"])
        self.assertEqual(len(list((self.vault / "cards/ideas/demand-research").glob("*.md"))), 3)

    def test_conflict_does_not_starve_later_batches_and_rechecks_changed_source(self):
        self.assertTrue(self.sync()["ok"])
        target = self.target()
        edited = target.read_text(encoding="utf-8") + "\nOwner notes\n"
        target.write_text(edited, encoding="utf-8")
        self.card.write_text("# Updated hypothesis\nNew source facts", encoding="utf-8")
        for index in range(2):
            (self.source / f"later-{index}.md").write_text(f"# Later {index}\nEvidence", encoding="utf-8")
        with patch.object(memory, "MAX_CARDS", 1):
            results = [self.sync(), self.sync(), self.sync()]
        self.assertEqual([result["pending"] for result in results], [2, 1, 0])
        self.assertEqual([result["created"] for result in results], [0, 1, 1])
        self.assertTrue(all(result["conflicts"] for result in results))
        self.assertEqual(target.read_text(encoding="utf-8"), edited)
        self.card.write_text("# Changed once more\nMore source facts", encoding="utf-8")
        with patch.object(memory.subprocess, "run", wraps=subprocess.run) as run:
            final = self.sync()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(final["conflicts"][0]["reason"], "manual_or_unmanaged_changes")

    def test_corrupt_index_revalidates_targets_without_overwriting(self):
        self.assertTrue(self.sync()["ok"])
        before = self.target().read_bytes()
        index_path, _ = memory._index_location(self.source, self.vault)
        index_path.write_text("broken index", encoding="utf-8")
        result = self.sync()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(self.target().read_bytes(), before)

    def test_native_memory_search_finds_exported_idea_recursively(self):
        self.card.write_text("# DEMANDLIGHTHOUSE8427\n\nИсточник: https://example.com/original\n", encoding="utf-8")
        self.assertTrue(self.sync()["ok"])
        script = r"""
import {registerHooks} from 'node:module';
import {existsSync, readFileSync} from 'node:fs';
import {fileURLToPath, pathToFileURL} from 'node:url';
registerHooks({resolve(specifier, context, nextResolve) {
  if (specifier.endsWith('.js') && specifier.startsWith('.') && context.parentURL) {
    const source = new URL(specifier.slice(0,-3) + '.ts', context.parentURL);
    if (source.protocol === 'file:' && existsSync(fileURLToPath(source)))
      return nextResolve(source.href, context);
  }
  return nextResolve(specifier, context);
}});
const modulePath = JSON.parse(readFileSync(0, 'utf8'));
const {searchMemory} = await import(pathToFileURL(modulePath).href);
console.log(JSON.stringify(await searchMemory({query: 'DEMANDLIGHTHOUSE8427', limit: 3})));
"""
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG"}}
        env["ASSISTANT_VAULT_DIR"] = str(self.vault)
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            input=json.dumps(str(memory.NATIVE_LIB.parent / "tools/memory_search.ts")),
            text=True, encoding="utf-8", capture_output=True, timeout=15, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["hits"], result)
        self.assertIn("cards/ideas/demand-research/", result["hits"][0]["file"].replace("\\", "/"))
        self.assertEqual(result["hits"][0]["status"], "draft")

    def test_changed_source_updates_only_owned_document(self):
        unrelated = self.vault / "cards/ideas/manual.md"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("owner notes", encoding="utf-8")
        self.assertTrue(self.sync()["ok"])
        self.card.write_text("# Хранение инструмента\n\nНовое контрсвидетельство.\n", encoding="utf-8")
        result = self.sync()
        self.assertEqual(result["updated"], 1)
        self.assertIn("Новое контрсвидетельство", self.target().read_text(encoding="utf-8"))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "owner notes")

    def test_owner_notes_are_preserved_as_reported_conflict(self):
        self.assertTrue(self.sync()["ok"])
        target = self.target()
        edited = target.read_text(encoding="utf-8") + "\n## Мои заметки\nНе удалять\n"
        target.write_text(edited, encoding="utf-8")
        self.card.write_text("# Другая версия\nНовое содержание", encoding="utf-8")
        result = self.sync()
        self.assertFalse(result["ok"])
        self.assertEqual(result["conflicts"][0]["reason"], "manual_or_unmanaged_changes")
        self.assertEqual(target.read_text(encoding="utf-8"), edited)

    def test_unmanaged_collision_is_not_overwritten(self):
        target_dir = self.vault / "cards/ideas/demand-research"
        target_dir.mkdir(parents=True)
        name = "demand-" + hashlib.sha256(self.card.name.encode()).hexdigest()[:24] + ".md"
        target = target_dir / name
        target.write_text("manually created", encoding="utf-8")
        result = self.sync()
        self.assertFalse(result["ok"])
        self.assertEqual(target.read_text(encoding="utf-8"), "manually created")

    def test_missing_source_keeps_existing_memory(self):
        self.assertTrue(self.sync()["ok"])
        before = self.target().read_bytes()
        self.card.unlink()
        result = self.sync()
        self.assertTrue(result["ok"])
        self.assertEqual(self.target().read_bytes(), before)

    def test_source_content_cannot_set_target_type_or_path(self):
        self.card.write_text("---\ntype: decision\npath: ../../contacts/person.md\n---\n"
                             "# Заголовок\nМатериал", encoding="utf-8")
        result = self.sync()
        self.assertTrue(result["ok"], result)
        output = self.target().read_text(encoding="utf-8")
        frontmatter = output.split("---\n", 2)[1]
        fields = dict(line.split(": ", 1) for line in frontmatter.splitlines())
        self.assertEqual(json.loads(fields["type"]), "idea")
        self.assertEqual(json.loads(fields["status"]), "draft")
        self.assertFalse((self.vault / "cards/contacts").exists())

    def test_active_native_lock_prevents_writes_without_removing_owner(self):
        lock = self.vault / "cards/.write_card.lock"
        lock.mkdir(parents=True)
        owner = lock / (".owner-" + str(uuid.uuid4()))
        owner.write_text("", encoding="utf-8")
        result = self.sync()
        self.assertFalse(result["ok"])
        self.assertTrue(owner.exists())
        self.assertEqual(list((self.vault / "cards/ideas/demand-research").glob("*.md")), [])

    def test_source_limit_prevents_any_export(self):
        self.card.write_bytes(b"x" * (memory.MAX_CARD_BYTES + 1))
        result = self.sync()
        self.assertEqual(result["errors"][0]["reason"], "source_size_limit")
        self.assertFalse(self.vault.exists())

    def test_timeout_is_explicit_unknown_outcome(self):
        with patch.object(memory.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 1)):
            result = self.sync()
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["reason"], "export_timeout_outcome_unknown_retry_safe")

    @unittest.skipIf(os.name == "nt", "Windows symlink creation requires extra privileges")
    def test_symlink_destination_does_not_write_outside_vault(self):
        external = self.root / "external"
        external.mkdir()
        self.vault.mkdir()
        (self.vault / "cards").symlink_to(external, target_is_directory=True)
        result = self.sync()
        self.assertFalse(result["ok"])
        self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
