import { z } from "zod";
import { resolveAt, ownerTimeZone } from "./reminder-time.ts";
import { formatZoned } from "./zoned-time.ts";
export const GOAL_MAX_OPEN = 5,
  GOAL_CHECKS_PER_DAY = 8,
  GOAL_FAILURES_TO_PAUSE = 3;
export const GOAL_DEFAULT_INTERVAL_MS = 7200000,
  GOAL_MIN_INTERVAL_MS = 1800000;
export const GOAL_MAX_INTERVAL_MS = 604800000,
  GOAL_LIFETIME_MS = 2592000000;
const ms = z.number().int().nonnegative();
export const helperIdSchema = z.string().regex(/^h-[0-9a-f]{6}$/);
export const goalVerdictSchema = z.strictObject({
  verdict: z.enum(["quiet", "news", "done", "blocked"]),
  plan: z.string().max(1200),
  nextCheckAtMs: ms.nullable(),
});
export const goalSchema = z.strictObject({
  id: z.string().regex(/^g-[0-9a-f]{6}$/),
  text: z.string().trim().min(1).max(500),
  may: z.string().max(500),
  chat: z.strictObject({
    id: z.string().min(1),
    threadId: z.string().min(1).nullable(),
  }),
  createdAt: ms,
  status: z.enum(["active", "proposed", "paused"]),
  pause: z.enum(["failures", "expired"]).nullable(),
  plan: goalVerdictSchema.shape.plan,
  nextCheckAtMs: goalVerdictSchema.shape.nextCheckAtMs,
  ownerNext: z.boolean(),
  check: helperIdSchema.nullable(),
  verdict: goalVerdictSchema.nullable(),
  checks: z.strictObject({
    day: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    count: ms,
  }),
  failures: ms,
  lastError: z.string().max(500).nullable(),
  expiresAtMs: ms,
});
export type Goal = z.infer<typeof goalSchema>;
export type GoalVerdict = z.infer<typeof goalVerdictSchema>;
export const goalInputSchema = z.strictObject({
  action: z.enum(["start", "list", "update", "resume", "close", "verdict"]),
  id: goalSchema.shape.id.optional(),
  text: goalSchema.shape.text.optional(),
  may: goalSchema.shape.may.optional(),
  plan: goalSchema.shape.plan.optional(),
  verdict: goalVerdictSchema.shape.verdict.optional(),
  next_check: z.string().optional(),
});
export type GoalInput = z.infer<typeof goalInputSchema>;
export const goalDay = (now: number, tz = ownerTimeZone()) =>
  formatZoned(now, tz).slice(0, 10);
export function resolveGoalTime(value: string, now: number, tz?: string) {
  const at =
    value === "now" ? now : resolveAt(value, now, tz ?? ownerTimeZone());
  if (at > now + GOAL_MAX_INTERVAL_MS)
    throw Error("next_check: срок дальше горизонта goal");
  return at;
}
type GoalRow = Record<"id" | "status", string> &
  Record<"goal" | "outcome" | "reason", string | null>;
export function settleGoal(s: { goals: Goal[] }, r: GoalRow, now: number) {
  const g = s.goals.find((g) => g.id === r.goal);
  if (!g || g.check !== r.id || r.outcome === "stopped") {
    if (g?.check === r.id) g.check = g.verdict = null;
    r.status = "closed";
    return "detached";
  }
  const v = g.verdict;
  g.check = g.verdict = null;
  if (v && v.verdict !== "blocked") {
    Object.assign(g, { plan: v.plan, failures: 0 });
    if (v.verdict === "done")
      Object.assign(g, { status: "proposed", nextCheckAtMs: null });
    else if (!g.ownerNext && v.nextCheckAtMs !== null)
      g.nextCheckAtMs = Math.max(
        now + GOAL_MIN_INTERVAL_MS,
        Math.min(now + GOAL_MAX_INTERVAL_MS, v.nextCheckAtMs),
      );
    if (v.verdict === "quiet") r.status = "closed";
    return v.verdict;
  }
  g.failures++;
  if (v) g.plan = v.plan;
  g.lastError = (v?.plan ?? r.reason)?.slice(0, 500) ?? null;
  if (g.failures >= GOAL_FAILURES_TO_PAUSE) {
    g.status = "paused";
    g.pause = "failures";
    g.nextCheckAtMs = null;
  } else r.status = "closed";
  return "failure";
}
