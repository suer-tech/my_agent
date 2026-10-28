/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns registration. */
import assert from "node:assert/strict";
import test from "node:test";
import {
  mkdtemp,
  rm,
  readFile,
  writeFile,
  stat,
  mkdir,
  readdir,
} from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import fc from "fast-check";
import * as s from "./helper-store.ts";
const chat = { id: "42", threadId: null };
const other = { id: "43", threadId: null };
const finish = {
  outcome: "done" as const,
  summary: "result",
  reason: null,
  model: "fixture",
  steps: 1,
  tokens: 12,
};
async function data(t: test.TestContext) {
  const dir = await mkdtemp(join(tmpdir(), "iva-helper-"));
  const prev = process.env.ASSISTANT_DATA_DIR;
  process.env.ASSISTANT_DATA_DIR = dir;
  t.after(async () => {
    if (prev === undefined) delete process.env.ASSISTANT_DATA_DIR;
    else process.env.ASSISTANT_DATA_DIR = prev;
    await rm(dir, { recursive: true, force: true });
  });
  return dir;
}
async function added(now = 1000) {
  const r = await s.addHelper({ task: "task", may: "", chat }, now);
  assert.ok(r.ok, r.ok ? undefined : r.text);
  return r.row;
}
async function ready(now = 1000) {
  const r = await added(now);
  await s.finalizeHelper(
    r.id,
    { kind: "pending", createdAt: now },
    finish,
    now + 1,
  );
  return r;
}
test("missing reader and schema corruption preserve bytes and reject foreign fields/types/duplicates/version", async (t) => {
  const dir = await data(t);
  assert.deepEqual(await s.readHelpers(), {
    schemaVersion: 1,
    rows: [],
    goals: [],
  });
  const row = await added();
  const base = await s.readHelpers();
  await fc.assert(
    fc.asyncProperty(
      fc.oneof(fc.jsonValue(), fc.constantFrom("{", "null", "[]")),
      async (value) => {
        const raw = typeof value === "string" ? value : JSON.stringify(value);
        await writeFile(join(dir, "helpers.json"), raw);
        await assert.rejects(s.readHelpers(), s.HelperStoreError);
        assert.equal(await readFile(join(dir, "helpers.json"), "utf8"), raw);
      },
    ),
    { seed: 20261013, numRuns: 40 },
  );
  for (const value of [{ ...base, schemaVersion: 2 }]) {
    const raw = JSON.stringify(value);
    await writeFile(join(dir, "helpers.json"), raw);
    await assert.rejects(s.readHelpers(), s.HelperStoreError);
    assert.equal(await readFile(join(dir, "helpers.json"), "utf8"), raw);
  }
  for (const bad of [
    { ...row, x: 1 },
    { ...row, steps: 0.5 },
    { ...row, status: "finished" },
  ]) {
    await writeFile(
      join(dir, "helpers.json"),
      JSON.stringify({ ...base, rows: [row, bad] }),
    );
    assert.deepEqual((await s.readHelpers()).rows, [row]);
    const copies = (await readdir(dir)).filter((name) =>
      name.includes(".damaged-"),
    );
    const records = await Promise.all(
      copies.map(
        async (name) =>
          (
            JSON.parse(await readFile(join(dir, name), "utf8")) as {
              record: unknown;
            }
          ).record,
      ),
    );
    assert.ok(
      records.some((record) => JSON.stringify(record) === JSON.stringify(bad)),
    );
  }
  await writeFile(
    join(dir, "helpers.json"),
    JSON.stringify({ ...base, rows: [row, row] }),
  );
  assert.deepEqual((await s.readHelpers()).rows, [row]);
  assert.deepEqual(
    (await readdir(dir)).filter((x) => x.includes("corrupt")),
    [],
  );
});
test("input rejection before directory; empty may; capacity counts pending, max rows includes outcomes", async (t) => {
  const dir = await data(t);
  for (const input of [
    { task: "t", chat },
    { task: " ", may: "", chat },
    { task: "x", may: "x".repeat(501), chat },
    { task: "x".repeat(2001), may: "", chat },
  ])
    assert.equal(
      (await s.addHelper(input as Parameters<typeof s.addHelper>[0], 1000)).ok,
      false,
    );
  assert.deepEqual(await s.readHelpers(), {
    schemaVersion: 1,
    rows: [],
    goals: [],
  });
  assert.deepEqual(
    (await readdir(dir)).filter((x) => x === "helpers"),
    [],
  );
  const rows = await Promise.all([added(), added(), added()]);
  const refused = await s.addHelper({ task: "four", may: "", chat }, 1000);
  assert.equal(refused.ok, false);
  if (!refused.ok) assert.equal(refused.reason, "capacity");
  for (const r of rows)
    await s.finalizeHelper(
      r.id,
      { kind: "pending", createdAt: 1000 },
      finish,
      1001,
    );
  for (let i = 3; i < 20; i++) await ready(1000 + i);
  assert.equal((await s.readHelpers()).rows.length, 20);
  const full = await s.addHelper({ task: "full", may: "", chat }, 2000);
  assert.equal(full.ok, false);
  if (!full.ok) {
    assert.equal(full.reason, "max-rows");
    assert.match(full.text, /20/);
    assert.match(full.text, /h-/);
  }
  assert.equal((await stat(join(dir, "helpers.json"))).mode & 0o777, 0o600);
  assert.equal(new Set(rows.map((r) => r.id)).size, 3);
  await assert.rejects(stat(join(dir, "helpers")), { code: "ENOENT" });
});
test("claim/fired CAS, repeated client T3, first stop and finalize cannot resurrect/overwrite", async (t) => {
  const dir = await data(t);
  const a = await added();
  const b = await added();
  const owner = { ownerStartedAt: 10 };
  const claims = await Promise.all([
    s.claimHelper(a.id, owner, 1100),
    s.claimHelper(b.id, owner, 1100),
  ]);
  assert.equal(claims.filter(Boolean).length, 1);
  const row = claims.find(Boolean)!;
  assert.equal(await s.recordHelperSession(row.id, 1099, "stale"), false);
  assert.equal(await s.recordHelperSession(row.id, 1100, "server"), true);
  assert.equal(await s.recordHelperSession(row.id, 1100, "server"), true);
  assert.equal(await s.recordHelperSession(row.id, 1100, "other"), false);
  await s.stopHelper(row.id, 1100, "closed");
  await s.stopHelper(row.id, 1100, "deadline");
  assert.equal(
    await s.finalizeHelper(
      row.id,
      { kind: "pending", createdAt: 1000 },
      finish,
      1200,
    ),
    null,
  );
  const done = await s.finalizeHelper(
    row.id,
    { kind: "running", firedAt: 1100 },
    finish,
    1200,
  );
  assert.equal(done?.outcome, "stopped");
  assert.equal(done?.status, "closed");
  assert.equal(done?.summary, "result");
  assert.equal(done?.run, null);
  assert.equal(
    await s.finalizeHelper(
      row.id,
      { kind: "running", firedAt: 1100 },
      { ...finish, summary: "late" },
      1201,
    ),
    null,
  );
  assert.equal((await s.getHelper(row.id))?.summary, "result");
  assert.deepEqual(await readdir(join(dir, "trace")).catch(() => []), []);
});
test("pure read of multiple outcomes; delivery timestamp once by id/chat, sweep clocks", async (t) => {
  const dir = await data(t);
  const a = await ready();
  const b = await ready(1002);
  const before = await readFile(join(dir, "helpers.json"), "utf8");
  await assert.doesNotReject(() => s.showHelper(a.id, chat));
  assert.equal((await s.showHelper(a.id, other)).ok, false);
  for (const id of [a.id, b.id, a.id])
    assert.equal((await s.showHelper(id, chat)).ok, true);
  assert.equal(await readFile(join(dir, "helpers.json"), "utf8"), before);
  assert.equal(await s.markHelperDelivered(a.id, other, 2000), null);
  assert.equal((await s.markHelperDelivered(a.id, chat, 2000))?.id, a.id);
  assert.equal((await s.markHelperDelivered(b.id, chat, 2001))?.id, b.id);
  assert.equal(await s.markHelperDelivered(a.id, chat, 2002), null);
  assert.equal((await s.getHelper(b.id))?.deliveredAt, 2001);
  await mkdir(join(dir, "helpers", a.id, "out"), { recursive: true });
  await writeFile(s.helperResultPath(a.id), "full result");
  const delivered = await readFile(join(dir, "helpers.json"), "utf8");
  await s.showHelper(a.id, chat);
  assert.equal(await readFile(join(dir, "helpers.json"), "utf8"), delivered);
  assert.deepEqual(await s.sweepHelpers(2000 + s.HELPER_KEEP_MS - 1), []);
  assert.deepEqual(await s.sweepHelpers(2000 + s.HELPER_KEEP_MS), [a.id]);
  await assert.rejects(stat(join(dir, "helpers", a.id)), { code: "ENOENT" });
  assert.deepEqual(await s.sweepHelpers(2001 + s.HELPER_KEEP_MS), [b.id]);
  const c = await ready(3000);
  const d = await ready(4000);
  await s.closeHelper(c.id, chat, 5000);
  assert.deepEqual(await s.sweepHelpers(3001 + s.HELPER_KEEP_MS - 1), []);
  assert.deepEqual(await s.sweepHelpers(3001 + s.HELPER_KEEP_MS), [c.id]);
  assert.ok(await s.getHelper(d.id));
});
test("three close receipts and stop-in-flight remain pending for confirmed server end", async (t) => {
  await data(t);
  const a = await added();
  const b = await added();
  const c = await ready();
  await s.claimHelper(b.id, { ownerStartedAt: 10 }, 2000);
  const results = await Promise.all([
    s.closeHelper(a.id, chat, 2100),
    s.closeHelper(b.id, chat, 2100),
    s.closeHelper(c.id, chat, 2100),
  ]);
  assert.deepEqual(
    results.map((r) => (r.ok ? r.branch : "error")),
    ["pending", "running", "finished"],
  );
  const shown = await s.showHelper(b.id, chat);
  assert.equal(shown.ok, true);
  if (shown.ok) {
    assert.equal(shown.row.outcome, null);
    assert.ok(shown.row.run);
  }
  assert.equal((await s.closeHelper(c.id, other, 2102)).ok, false);
});
test("command sequences preserve capacity, stop, receipts and undelivered lifetime (seed 20261013)", async (t) => {
  const dir = await data(t);
  await fc.assert(
    fc.asyncProperty(
      fc.array(fc.integer({ min: 0, max: 8 }), { maxLength: 60 }),
      async (commands) => {
        await rm(join(dir, "helpers"), { force: true, recursive: true });
        await rm(join(dir, "helpers.json"), { force: true });
        let now = 1000;
        for (const cmd of commands) {
          now++;
          const rows = (await s.readHelpers()).rows;
          const r = rows[cmd % Math.max(1, rows.length)];
          if (cmd === 0)
            await s.addHelper({ task: "pbt", may: "opaque", chat }, now);
          else if (r) {
            if (cmd === 1)
              await s.claimHelper(r.id, { ownerStartedAt: 10 }, now);
            if (cmd === 2 && r.run)
              await s.stopHelper(r.id, r.run.firedAt, "deadline");
            if (cmd === 3)
              await s.finalizeHelper(
                r.id,
                r.run
                  ? { kind: "running", firedAt: r.run.firedAt }
                  : { kind: "pending", createdAt: r.createdAt },
                finish,
                now,
              );
            if (cmd === 4) await s.closeHelper(r.id, chat, now);
            if (cmd === 5) await s.showHelper(r.id, chat);
            if (cmd === 6) await s.markHelperDelivered(r.id, chat, now);
            if (cmd === 8) await s.sweepHelpers(now);
          }
          const state = await s.readHelpers();
          assert.ok(
            state.rows.filter((r) => r.status === "pending").length <= 3,
          );
          assert.ok(state.rows.filter((r) => r.run).length <= 1);
          for (const row of state.rows) {
            assert.equal(row.may, "opaque");
            if (row.outcome) assert.equal(row.run, null);
          }
        }
      },
    ),
    { seed: 20261013, numRuns: 30 },
  );
});

test("POISON helpers and goals keep good rows and unknown top-level fields across mutation", async (t) => {
  const dir = await data(t);
  const good = await added();
  const file = join(dir, "helpers.json");
  const goal = {
    id: "g-abcdef",
    text: "goal",
    may: "",
    chat,
    createdAt: 1,
    status: "active",
    pause: null,
    plan: "",
    nextCheckAtMs: 10,
    ownerNext: false,
    check: null,
    verdict: null,
    checks: { day: "2026-10-16", count: 0 },
    failures: 0,
    lastError: null,
    expiresAtMs: 1000,
  };
  const bad = [
    { ...good, task: "private helper", extra: true },
    { ...goal, text: "private goal", extra: true },
  ];
  await writeFile(
    file,
    JSON.stringify({
      schemaVersion: 1,
      rows: [bad[0], good],
      goals: [bad[1], goal],
      future: { secret: "preserve" },
    }),
  );
  const logs: string[] = [];
  t.mock.method(console, "error", (...parts: unknown[]) =>
    logs.push(parts.map(String).join(" ")),
  );
  for (let i = 0; i < 2; i++) {
    const state = await s.readHelpers();
    assert.deepEqual(state.rows, [good]);
    assert.deepEqual(state.goals, [goal]);
  }
  const copies = (await readdir(dir)).filter((name) =>
    /\.damaged-[a-f0-9]{16}\.json$/.test(name),
  );
  assert.equal(copies.length, 2);
  assert.equal(logs.length, 2);
  assert.ok(
    logs.every(
      (line) =>
        !line.includes("private helper") && !line.includes("private goal"),
    ),
  );
  const records = await Promise.all(
    copies.map(
      async (name) =>
        (
          JSON.parse(await readFile(join(dir, name), "utf8")) as {
            record: unknown;
          }
        ).record,
    ),
  );
  assert.deepEqual(
    records.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    [...bad].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
  );
  await s.mutateHelpers((state) => {
    state.rows[0].may = "updated";
    return { save: true, value: null };
  });
  const saved = JSON.parse(await readFile(file, "utf8")) as {
    future: unknown;
    rows: unknown[];
    goals: unknown[];
  };
  assert.deepEqual(saved.future, { secret: "preserve" });
  assert.equal(saved.rows.length, 1);
  assert.deepEqual(saved.goals, [goal]);
});

test("REPAIR goal survives damaged check and quarantines only offending records", async (t) => {
  const dir = await data(t);
  const row = await added();
  const goal = {
    id: "g-bbbbbb",
    text: "goal",
    may: "",
    chat,
    createdAt: 1,
    status: "active",
    pause: null,
    plan: "keep plan",
    nextCheckAtMs: 10,
    ownerNext: false,
    check: "h-abcdef",
    verdict: { verdict: "done", plan: "stale", nextCheckAtMs: null },
    checks: { day: "2026-10-16", count: 1 },
    failures: 0,
    lastError: null,
    expiresAtMs: 1000,
  };
  const linked = { ...row, id: "h-abcdef", goal: goal.id };
  const file = join(dir, "helpers.json");
  const completed = Array.from({ length: 21 }, (_, index) => ({
    ...row,
    id: `h-${index.toString(16).padStart(6, "0")}`,
    status: "finished",
    outcome: "done",
    finishedAt: 10,
  }));
  const cases = [
    {
      rows: [row, { ...linked, steps: 0.5 }],
      goals: [goal],
      bad: [{ ...linked, steps: 0.5 }],
    },
    {
      rows: [row, linked],
      goals: [goal, { ...goal, text: "duplicate" }],
      bad: [{ ...goal, text: "duplicate" }],
    },
    {
      rows: [row, { ...linked, goal: "g-cccccc" }],
      goals: [goal],
      bad: [],
    },
    {
      rows: [
        row,
        linked,
        { ...row, id: "h-111111" },
        { ...row, id: "h-222222" },
      ],
      goals: [goal],
      bad: [{ ...row, id: "h-222222" }],
    },
    {
      rows: [row, linked, { ...row, task: "duplicate helper" }],
      goals: [goal],
      bad: [{ ...row, task: "duplicate helper" }],
    },
    {
      rows: [row, { ...linked, goal: null }],
      goals: [goal],
      bad: [],
    },
    { rows: completed, goals: [goal], bad: [completed[20]] },
  ];
  for (const value of cases) {
    await writeFile(
      file,
      JSON.stringify({
        schemaVersion: 1,
        rows: value.rows,
        goals: value.goals,
      }),
    );
    const state = await s.readHelpers();
    assert.equal(state.goals.length, 1);
    assert.deepEqual(state.goals[0], {
      ...goal,
      ...(value.rows.some((r) => r === linked)
        ? {}
        : { check: null, verdict: null }),
    });
    assert.ok(
      state.rows.every(
        (r) => r.goal === null || state.goals.some((g) => g.id === r.goal),
      ),
    );
    assert.equal(
      state.rows.length,
      value.rows.length - value.bad.filter((r) => "steps" in r).length,
    );
    const copies = (await readdir(dir)).filter((name) =>
      name.includes(".damaged-"),
    );
    const records = await Promise.all(
      copies.map(
        async (name) =>
          (
            JSON.parse(await readFile(join(dir, name), "utf8")) as {
              record: unknown;
            }
          ).record,
      ),
    );
    for (const bad of value.bad)
      assert.ok(records.some((r) => JSON.stringify(r) === JSON.stringify(bad)));
    assert.ok(
      records.every(
        (r) =>
          (r as { id: string }).id !== goal.id ||
          (r as { text: string }).text === "duplicate",
      ),
    );
    await s.readHelpers();
    assert.deepEqual(
      (await readdir(dir)).filter((name) => name.includes(".damaged-")),
      copies,
    );
    await s.mutateHelpers(() => ({ save: true, value: null }));
    assert.deepEqual(JSON.parse(await readFile(file, "utf8")), state);
  }
});

test("REPAIR3 orphan helper survives a damaged or missing goal without quarantine", async (t) => {
  const dir = await data(t);
  const row = await added();
  const goal = {
    id: "g-bbbbbb",
    text: "goal",
    may: "",
    chat,
    createdAt: 1,
    status: "active",
    pause: null,
    plan: "plan",
    nextCheckAtMs: 10,
    ownerNext: false,
    check: "h-abcdef",
    verdict: null,
    checks: { day: "2026-10-16", count: 1 },
    failures: 0,
    lastError: null,
    expiresAtMs: 1000,
  };
  const linked = { ...row, id: "h-abcdef", goal: goal.id };
  const file = join(dir, "helpers.json");
  const bad = { ...goal, extra: 1 };
  const logs: unknown[][] = [];
  t.mock.method(console, "error", (...parts: unknown[]) => logs.push(parts));
  for (const goals of [[], [bad]]) {
    const raw = JSON.stringify({
      schemaVersion: 1,
      rows: [row, linked],
      goals,
    });
    await writeFile(file, raw);
    const expected = {
      schemaVersion: 1,
      rows: [row, { ...linked, goal: null }],
      goals: [],
    };
    for (let i = 0; i < 2; i++)
      assert.deepEqual(await s.readHelpers(), expected);
    assert.equal(await readFile(file, "utf8"), raw);
    const copies = (await readdir(dir)).filter((name) =>
      name.includes(".damaged-"),
    );
    assert.equal(copies.length, goals.length);
    if (goals.length)
      assert.deepEqual(
        (
          JSON.parse(await readFile(join(dir, copies[0]), "utf8")) as {
            record: unknown;
          }
        ).record,
        bad,
      );
    assert.equal(logs.length, goals.length);
    await s.mutateHelpers(() => ({ save: true, value: null }));
    assert.deepEqual(JSON.parse(await readFile(file, "utf8")), expected);
  }
});
