/* eslint-disable @typescript-eslint/no-floating-promises -- Node test registration. */
import "./fixtures/no-host-anthropic.ts";
import "./lib/ts-esm-hooks.ts";
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, writeFile, rm, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import * as s from "../agent/lib/helper-store.ts";
import {
  readHelperOutcome,
  noteHelperReply,
  finishHelperOutcomes,
  helperOutcomesMarkdown,
} from "../agent/lib/helper-outcome.ts";
const chat = { id: "41", threadId: null },
  key = { sessionId: "owner", turnId: "turn_0" };
async function fixture(run: (row: s.HelperRow, dir: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), "iva-outcome-"));
  const old = process.env.ASSISTANT_DATA_DIR;
  process.env.ASSISTANT_DATA_DIR = dir;
  try {
    const r = await s.addHelper(
      { task: "Find the document", may: "", chat },
      1000,
    );
    assert.ok(r.ok);
    const row = await s.finalizeHelper(
      r.row.id,
      { kind: "pending", createdAt: 1000 },
      {
        outcome: "done",
        summary: "Document found",
        reason: null,
        model: "fixture",
        steps: 1,
        tokens: 12,
      },
      2000,
    );
    assert.ok(row);
    await run(row, dir);
  } finally {
    await finishHelperOutcomes(key, 4000);
    if (old === undefined) delete process.env.ASSISTANT_DATA_DIR;
    else process.env.ASSISTANT_DATA_DIR = old;
    await rm(dir, { recursive: true, force: true });
  }
}
test("two outcome reads are pure and both mark only after a delivered reply and turn completion", async () =>
  fixture(async (row, dir) => {
    const r = await s.addHelper({ task: "Second", may: "", chat }, 1001);
    assert.ok(r.ok);
    const second = await s.finalizeHelper(
      r.row.id,
      { kind: "pending", createdAt: 1001 },
      {
        outcome: "empty",
        summary: null,
        reason: "nothing",
        model: null,
        steps: 0,
        tokens: 0,
      },
      2001,
    );
    assert.ok(second);
    const before = await readFile(join(dir, "helpers.json"));
    const envelope = JSON.parse(
      await readHelperOutcome(row.id, chat, key),
    ) as Record<string, unknown>;
    assert.equal(envelope.full_text_path, null);
    assert.equal("more_ready" in envelope, false);
    assert.equal(envelope.asked_at, new Date(1000).toISOString());
    assert.equal(envelope.finished_at, new Date(2000).toISOString());
    await readHelperOutcome(second.id, chat, key);
    assert.deepEqual(await readFile(join(dir, "helpers.json")), before);
    noteHelperReply(key, "Here are both findings");
    assert.equal((await s.getHelper(row.id))?.deliveredAt, null);
    await finishHelperOutcomes({ ...key, sessionId: "another" }, 3000);
    assert.equal((await s.getHelper(row.id))?.deliveredAt, null);
    await finishHelperOutcomes(key, 3000);
    for (const id of [row.id, second.id])
      assert.equal((await s.getHelper(id))?.deliveredAt, 3000);
  }));
for (const ending of ["empty", "failure", "restart"] as const)
  test(`outcome stays visible after ${ending}`, async () =>
    fixture(async (row) => {
      await readHelperOutcome(row.id, chat, key);
      if (ending === "empty") noteHelperReply(key, "   ");

      await finishHelperOutcomes(
        ending === "empty" ? key : { ...key, sessionId: "other" },
        3000,
      );
      assert.equal((await s.getHelper(row.id))?.deliveredAt, null);
      assert.match(await helperOutcomesMarkdown(chat), new RegExp(row.id));
    }));
test("closed and delivered reads preserve the file; foreign chat and missing id preserve the file", async () =>
  fixture(async (row, dir) => {
    await s.markHelperDelivered(row.id, chat, 3000);
    const before = await readFile(join(dir, "helpers.json"));
    await readHelperOutcome(row.id, chat, key);
    await finishHelperOutcomes(key, 4000);
    await readHelperOutcome(row.id, { id: "42", threadId: null }, key);
    await readHelperOutcome("h-000000", chat, key);
    assert.deepEqual(await readFile(join(dir, "helpers.json")), before);
    assert.equal(await helperOutcomesMarkdown(chat), "");
  }));
test("existing full text has a path; a broken table is reported without moving its contents", async () =>
  fixture(async (row, dir) => {
    await mkdir(dirname(s.helperResultPath(row.id)), { recursive: true });
    await writeFile(s.helperResultPath(row.id), "Full text");
    assert.equal(
      (
        JSON.parse(await readHelperOutcome(row.id, chat, key)) as Record<
          string,
          unknown
        >
      ).full_text_path,
      s.helperResultPath(row.id),
    );
    await writeFile(join(dir, "helpers.json"), "broken");
    assert.match(
      await helperOutcomesMarkdown(chat),
      /таблица помощников повреждена/,
    );
    assert.equal(await readFile(join(dir, "helpers.json"), "utf8"), "broken");
  }));

test("closed reads are pure; running close waits for the partial outcome", async () =>
  fixture(async (row, dir) => {
    await s.closeHelper(row.id, chat, 2500);
    const before = await readFile(join(dir, "helpers.json"));
    await readHelperOutcome(row.id, chat, key);
    noteHelperReply(key, "read closed outcome");
    await finishHelperOutcomes(key, 3000);
    assert.deepEqual(await readFile(join(dir, "helpers.json")), before);
    const pending = await s.addHelper({ task: "Working", may: "", chat }, 1001);
    assert.ok(pending.ok);
    await s.claimHelper(pending.row.id, { ownerStartedAt: 500 }, 1500);
    await s.closeHelper(pending.row.id, chat, 2000);
    assert.match(
      await readHelperOutcome(pending.row.id, chat, key),
      /после остановки/,
    );
  }));
