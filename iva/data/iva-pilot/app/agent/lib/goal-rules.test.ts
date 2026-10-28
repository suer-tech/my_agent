/* eslint-disable @typescript-eslint/no-floating-promises -- Node test registrations. */
import "../../scripts/fixtures/no-host-anthropic.ts";
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import {
  goalSchema,
  settleGoal,
  resolveGoalTime,
  goalDay,
} from "./goal-rules.ts";
const now = Date.parse("2026-10-14T12:00:00Z");
const goal = () =>
  goalSchema.parse({
    id: "g-abcdef",
    text: "answer",
    may: "",
    chat: { id: "42", threadId: null },
    createdAt: now,
    status: "active",
    pause: null,
    plan: "old",
    nextCheckAtMs: now + 7200000,
    ownerNext: false,
    check: "h-abcdef",
    verdict: null,
    checks: { day: "2026-10-14", count: 1 },
    failures: 0,
    lastError: null,
    expiresAtMs: now + 2592000000,
  });
test("settlement follows the ordered verdict table for every outcome, owner time and detachment", () => {
  fc.assert(
    fc.property(
      fc.constantFrom(null, "quiet", "news", "done", "blocked"),
      fc.constantFrom(
        "done",
        "empty",
        "failed",
        "deadline",
        "ceiling",
        "interrupted",
        "stopped",
      ),
      fc.boolean(),
      fc.boolean(),
      fc.integer({ min: 0, max: 2 }),
      fc.string({ maxLength: 1200 }),
      fc.option(fc.integer({ min: now, max: now + 900000000 })),
      (
        verdict,
        outcome,
        detached,
        ownerNext,
        failures,
        plan,
        nextCheckAtMs,
      ) => {
        const g = goal();
        Object.assign(g, {
          ownerNext,
          failures,
          verdict: verdict === null ? null : { verdict, plan, nextCheckAtMs },
        });
        if (detached) g.check = null;
        const r = {
          id: "h-abcdef",
          goal: g.id,
          outcome,
          status: "finished",
          reason: "reason",
        };
        const before = structuredClone(g);
        const rule = settleGoal({ goals: [g] }, r, now);
        const ignored = detached || outcome === "stopped";
        const expected = ignored
          ? "detached"
          : verdict === "done"
            ? "done"
            : verdict === "news"
              ? "news"
              : verdict === "quiet"
                ? "quiet"
                : "failure";
        assert.equal(rule, expected);
        assert.equal(
          r.status,
          !ignored &&
            (verdict === "done" ||
              verdict === "news" ||
              (expected === "failure" && failures === 2))
            ? "finished"
            : "closed",
        );
        assert.equal(g.plan, ignored || verdict === null ? before.plan : plan);
        assert.equal(
          g.failures,
          ignored ? failures : expected === "failure" ? failures + 1 : 0,
        );
        assert.equal(
          g.status,
          ignored
            ? "active"
            : verdict === "done"
              ? "proposed"
              : expected === "failure" && failures === 2
                ? "paused"
                : "active",
        );
        if (g.status === "active") {
          const wanted =
            ignored ||
            ownerNext ||
            expected === "failure" ||
            nextCheckAtMs === null
              ? before.nextCheckAtMs
              : Math.max(
                  now + 1800000,
                  Math.min(now + 604800000, nextCheckAtMs),
                );
          assert.equal(g.nextCheckAtMs, wanted);
        } else assert.equal(g.nextCheckAtMs, null);
        if (!detached) assert.equal(g.check, null);
        goalSchema.parse(g);
      },
    ),
    { seed: 20261014, numRuns: 1000 },
  );
});
test("SPEC-FIX-4 long blocked plan and reason fit lastError while keeping the plan", () => {
  fc.assert(
    fc.property(
      fc.integer({ min: 501, max: 1200 }),
      fc.boolean(),
      (length, blocked) => {
        const g = goal();
        if (blocked)
          g.verdict = {
            verdict: "blocked",
            plan: "x".repeat(length),
            nextCheckAtMs: null,
          };
        const r = {
          id: "h-abcdef",
          goal: g.id,
          outcome: "failed",
          status: "finished",
          reason: "r".repeat(length * 2),
        };
        assert.equal(settleGoal({ goals: [g] }, r, now), "failure");
        assert.equal(g.lastError, (blocked ? "x" : "r").repeat(500));
        assert.equal(g.plan, blocked ? "x".repeat(length) : "old");
        goalSchema.parse(g);
      },
    ),
    { seed: 20261014, numRuns: 100 },
  );
});
test("next_check accepts existing forms and enforces the goal horizon", () => {
  for (const value of [
    "now",
    "in 30m",
    "14:30",
    "2026-10-15 09:00",
    "2026-10-15T09:00:00Z",
  ])
    assert.ok(resolveGoalTime(value, now, "UTC") >= now);
  fc.assert(
    fc.property(fc.integer({ min: 1, max: 604800 }), (seconds) => {
      assert.equal(
        resolveGoalTime(`in ${seconds}s`, now, "UTC"),
        now + seconds * 1000,
      );
    }),
    { seed: 20261014, numRuns: 100 },
  );
  for (const value of ["in 8d", "2026-10-13T12:00:00Z", "garbage", "in 0m"])
    assert.throws(() => resolveGoalTime(value, now, "UTC"));
});
test("owner day crosses midnight and timezone", () => {
  fc.assert(
    fc.property(fc.integer({ min: -60000, max: 60000 }), (delta) => {
      const time = Date.parse("2026-10-14T19:00:00Z") + delta;
      assert.equal(
        goalDay(time, "Asia/Tashkent"),
        delta < 0 ? "2026-10-14" : "2026-10-15",
      );
      assert.equal(goalDay(time, "UTC"), "2026-10-14");
    }),
    { seed: 20261014, numRuns: 100 },
  );
});
