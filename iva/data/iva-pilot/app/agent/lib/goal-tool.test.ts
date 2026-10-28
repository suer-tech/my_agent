/* eslint-disable @typescript-eslint/no-floating-promises -- Node test registrations. */
import "../../scripts/fixtures/no-host-anthropic.ts";
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { goalExecute } from "./goal-tool.ts";
import type { HelperToolContext } from "./helper-tool.ts";
import * as s from "./helper-store.ts";
import * as g from "./goal-store.ts";
const now = Date.parse("2026-10-14T12:00:00Z");
const auth = (kind: string, id = "h-abcdef") =>
  kind === "owner"
    ? {
        authenticator: "telegram-webhook",
        principalType: "user",
        attributes: { chat_type: "private", chat_id: "42" },
      }
    : {
        authenticator: "iva-bearer",
        attributes: { iva_turn: kind, iva_helper: id },
      };
const context = (kind: string, id?: string): HelperToolContext => ({
  session: {
    id: "test",
    turn: { id: "turn_0" },
    auth: { initiator: auth(kind, id), current: auth(kind, id) },
  },
});
async function setup(t: test.TestContext) {
  const dir = await mkdtemp(join(tmpdir(), "iva-goal-tool-")),
    prev = { ...process.env };
  Object.assign(process.env, {
    ASSISTANT_DATA_DIR: dir,
    ASSISTANT_TIMEZONE: "UTC",
  });
  t.after(async () => {
    for (const key of ["ASSISTANT_DATA_DIR", "ASSISTANT_TIMEZONE"])
      if (prev[key] === undefined) delete process.env[key];
      else process.env[key] = prev[key];
    await rm(dir, { recursive: true, force: true });
  });
}
test("private owner requires may and valid time; receipts carry timing data and trigger the tick", async (t) => {
  await setup(t);
  let ticks = 0;
  const deps = {
    now,
    tick: () => {
      ticks++;
      return Promise.resolve();
    },
  };
  assert.match(
    await goalExecute(
      { action: "start", text: "answer" },
      context("owner"),
      deps,
    ),
    /границ/,
  );
  assert.match(
    await goalExecute(
      { action: "start", text: "answer", may: "", next_check: "in 8d" },
      context("owner"),
      deps,
    ),
    /срок/,
  );
  assert.equal((await s.readHelpers()).goals.length, 0);
  const receipt = JSON.parse(
    await goalExecute(
      { action: "start", text: "answer", may: "" },
      context("owner"),
      deps,
    ),
  ) as Record<string, unknown>;
  assert.match(String(receipt.id), /^g-/);
  assert.equal(receipt.nextCheckAtMs, now);
  assert.equal(ticks, 1);
  assert.equal(receipt.quietHour, false);
  assert.equal(receipt.checks_today, 0);
  const id = String(receipt.id);
  assert.match(
    await goalExecute({ action: "resume", id }, context("owner"), deps),
    /уже идёт/,
  );
  const list = JSON.parse(
    await goalExecute({ action: "list" }, context("owner"), deps),
  ) as { untrusted: boolean; goals: Record<string, unknown>[] };
  assert.equal(list.untrusted, true);
  assert.equal(list.goals[0].id, id);
  assert.deepEqual(
    JSON.parse(await goalExecute({ action: "list" }, context("watch"), deps)),
    list,
  );
  assert.deepEqual(
    JSON.parse(await goalExecute({ action: "list" }, context("brief"), deps)),
    list,
  );
});
test("helper verdict CAS requires the same authenticated helper on both sides and its live check", async (t) => {
  await setup(t);
  const goal = await g.changeGoal(
    { action: "start", text: "answer", may: "" },
    { id: "42", threadId: null },
    null,
    now,
  );
  await g.dueGoals(now);
  const row = (await s.readHelpers()).rows[0];
  const deps = { now, tick: async () => {} };
  const input = {
    action: "verdict",
    id: goal.id,
    verdict: "news",
    plan: "found",
  };
  for (const action of ["start", "list", "update", "resume", "close"])
    assert.match(
      await goalExecute({ ...input, action }, context("helper", row.id), deps),
      /verdict/,
    );
  assert.match(await goalExecute(input, context("owner"), deps), /провер/);
  assert.match(
    await goalExecute(input, context("helper", "h-ffffff"), deps),
    /закрыта|отвязана/,
  );
  const mixed = context("helper", row.id);
  mixed.session.auth.current = auth("helper", "h-ffffff");
  assert.match(await goalExecute(input, mixed, deps), /провер/);
  await goalExecute(input, context("helper", row.id), deps);
  assert.equal((await s.readHelpers()).goals[0].verdict?.verdict, "news");
  await g.changeGoal(
    { action: "update", id: goal.id, plan: "new" },
    { id: "42", threadId: null },
    null,
    now,
  );
  assert.match(
    await goalExecute(input, context("helper", row.id), deps),
    /закрыта|отвязана/,
  );
});
test("unauthenticated and group calls return text without changing data; stale buttons refuse", async (t) => {
  await setup(t);
  const deps = { now, tick: async () => {} };
  const group = context("owner");
  group.session.auth.current = {
    ...auth("owner"),
    attributes: { chat_type: "group", chat_id: "42" },
  };
  for (const ctx of [undefined, {}, group]) {
    const text = await goalExecute(
      { action: "start", text: "answer", may: "" },
      ctx as HelperToolContext,
      deps,
    );
    assert.equal(typeof text, "string");
    assert.equal((await s.readHelpers()).goals.length, 0);
  }
  assert.match(
    await goalExecute(
      { action: "close", id: "g-abcdef" },
      context("owner"),
      deps,
    ),
    /уже закрыт/,
  );
});

test("QA2 close and resume ignore irrelevant invalid next_check", async (t) => {
  await setup(t);
  const deps = { now, tick: () => Promise.resolve() };
  for (const next_check of ["завтра", "2026-10-13T12:00:00Z", "in 10d"]) {
    const goal = await g.changeGoal(
      { action: "start", text: "answer", may: "" },
      { id: "42", threadId: null },
      null,
      now,
    );
    await s.mutateHelpers((state) => {
      Object.assign(state.goals[0], {
        status: "paused",
        pause: "expired",
        nextCheckAtMs: null,
      });
      return { save: true, value: null };
    });
    const resumed = JSON.parse(
      await goalExecute(
        { action: "resume", id: goal.id, next_check },
        context("owner"),
        deps,
      ),
    ) as Record<string, unknown>;
    assert.equal(resumed.nextCheckAtMs, now);
    assert.equal((await s.readHelpers()).goals[0].status, "active");
    await goalExecute(
      { action: "close", id: goal.id, next_check },
      context("owner"),
      deps,
    );
    assert.equal((await s.readHelpers()).goals.length, 0);
  }
});

test("QA5 verdict beyond seven days is accepted and clamped on settlement", async (t) => {
  await setup(t);
  const goal = await g.changeGoal(
    { action: "start", text: "answer", may: "" },
    { id: "42", threadId: null },
    null,
    now,
  );
  await g.dueGoals(now);
  const row = (await s.readHelpers()).rows[0];
  await s.claimHelper(row.id, { ownerStartedAt: now }, now);
  const receipt = JSON.parse(
    await goalExecute(
      {
        action: "verdict",
        id: goal.id,
        verdict: "quiet",
        plan: "observed",
        next_check: "in 10d",
      },
      context("helper", row.id),
      { now },
    ),
  ) as Record<string, unknown>;
  assert.equal(receipt.id, goal.id);
  assert.ok((await s.readHelpers()).goals[0].verdict);
  await s.finalizeHelper(
    row.id,
    { kind: "running", firedAt: now },
    {
      outcome: "done",
      summary: null,
      reason: null,
      model: "fixture",
      steps: 1,
      tokens: 1,
    },
    now + 1,
  );
  assert.equal(
    (await s.readHelpers()).goals[0].nextCheckAtMs,
    now + 1 + 604800000,
  );
});

test("QA5 close receipt confirms closure without the removed goal's schedule", async (t) => {
  await setup(t);
  const deps = { now, tick: () => Promise.resolve() };
  const goal = await g.changeGoal(
    { action: "start", text: "answer", may: "", next_check: "in 3h" },
    { id: "42", threadId: null },
    null,
    now,
  );
  const receipt = JSON.parse(
    await goalExecute({ action: "close", id: goal.id }, context("owner"), deps),
  ) as Record<string, unknown>;
  assert.deepEqual(receipt, { id: goal.id, closed: true });
  assert.equal((await s.readHelpers()).goals.length, 0);
});

test("QA5 generated verdict horizons over seven days remain valid tool inputs", async (t) => {
  await setup(t);
  const goal = await g.changeGoal(
    { action: "start", text: "answer", may: "" },
    { id: "42", threadId: null },
    null,
    now,
  );
  await g.dueGoals(now);
  const row = (await s.readHelpers()).rows[0];
  await s.claimHelper(row.id, { ownerStartedAt: now }, now);
  await fc.assert(
    fc.asyncProperty(fc.integer({ min: 169, max: 720 }), async (hours) => {
      const receipt = JSON.parse(
        await goalExecute(
          {
            action: "verdict",
            id: goal.id,
            verdict: "news",
            plan: "observed",
            next_check: `in ${hours}h`,
          },
          context("helper", row.id),
          { now },
        ),
      ) as Record<string, unknown>;
      assert.equal(receipt.id, goal.id);
      assert.equal(
        (await s.readHelpers()).goals[0].verdict?.nextCheckAtMs,
        now + hours * 3600000,
      );
    }),
    { seed: 20261014, numRuns: 30 },
  );
});

test("S3 map names the standing CORE goals", async () => {
  assert.match(
    await readFile(
      new URL("../instructions/10-map.md", import.meta.url),
      "utf8",
    ),
    /≤3 active CORE goals/,
  );
});
