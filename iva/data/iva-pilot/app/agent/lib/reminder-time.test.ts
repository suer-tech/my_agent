/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
import assert from "node:assert/strict";
import test from "node:test";
import {
  ReminderTimeError,
  nextCronRunMs,
  resolveAt,
} from "./reminder-time.ts";

// Зона владельца в примерах — Ташкент (UTC+5, без перехода на летнее время),
// now = 2026-09-12T05:00:00Z, то есть 10:00 на стене.
const TASHKENT = "Asia/Tashkent";
const NOW = Date.UTC(2026, 8, 12, 5, 0, 0);

function refused(
  input: string,
  pattern: RegExp,
  now = NOW,
  tz = TASHKENT,
): void {
  assert.throws(
    () => resolveAt(input, now, tz),
    (error: unknown) =>
      error instanceof ReminderTimeError && pattern.test(error.message),
    `${input} must be refused by ${pattern}`,
  );
}

test("resolveAt: relative and wall-clock forms in the owner zone", () => {
  assert.equal(resolveAt("in 30m", NOW, TASHKENT), NOW + 30 * 60_000);
  assert.equal(resolveAt("in 1h 30m", NOW, TASHKENT), NOW + 5_400_000);
  assert.equal(resolveAt("in 2d", NOW, TASHKENT), NOW + 172_800_000);
  assert.equal(resolveAt("IN 90S", NOW, TASHKENT), NOW + 90_000);
  assert.equal(resolveAt("14:30", NOW, TASHKENT), Date.UTC(2026, 8, 12, 9, 30));
  assert.equal(resolveAt("09:00", NOW, TASHKENT), Date.UTC(2026, 8, 13, 4, 0));
  assert.equal(
    resolveAt("2026-09-14 09:00", NOW, TASHKENT),
    Date.UTC(2026, 8, 14, 4, 0),
  );
  assert.equal(
    resolveAt("2026-09-14T09:00", NOW, TASHKENT),
    Date.UTC(2026, 8, 14, 4, 0),
  );
  assert.equal(
    resolveAt("2026-09-14 09:00", NOW, "Europe/Moscow"),
    Date.UTC(2026, 8, 14, 6, 0),
  );
  assert.equal(
    resolveAt("2026-09-14T09:00:00Z", NOW, TASHKENT),
    Date.UTC(2026, 8, 14, 9, 0),
  );
  assert.equal(
    resolveAt("2026-09-14T09:00:00+03:00", NOW, TASHKENT),
    Date.UTC(2026, 8, 14, 6, 0),
  );
});

test("resolveAt: refuses the past, junk, non-existent wall-clock time and a year ahead", () => {
  refused("in 0m", /duration must be positive/u);
  refused("2026-02-31 09:00", /no such wall-clock time in Asia\/Tashkent/u);
  refused("2026-09-12 09:00", /2026-09-12 09:00 is in the past/u);
  refused("завтра в 9", /unsupported form/u);
  refused("2027-12-01 09:00", /more than a year ahead/u);
});

test("nextCronRunMs: next run in the owner zone, strictly after the anchor", () => {
  assert.equal(
    nextCronRunMs("0 9 * * 1-5", TASHKENT, NOW),
    Date.UTC(2026, 8, 14, 4, 0),
  );
  assert.equal(
    nextCronRunMs("0 9 * * *", "America/New_York", NOW),
    Date.UTC(2026, 8, 12, 13, 0),
  );
  assert.equal(
    nextCronRunMs("0 9 * * *", "Europe/Berlin", Date.UTC(2026, 2, 28, 12)),
    Date.UTC(2026, 2, 29, 7),
  );
  assert.equal(
    nextCronRunMs("15 * * * *", "UTC", Date.UTC(2026, 8, 12, 10, 0, 30)),
    Date.UTC(2026, 8, 12, 10, 15),
  );
  assert.equal(
    nextCronRunMs("15 * * * *", "UTC", Date.UTC(2026, 8, 12, 10, 15)),
    Date.UTC(2026, 8, 12, 11, 15),
  );
});

test("nextCronRunMs: refuses spam cadence and expressions that never fire", () => {
  assert.throws(
    () => nextCronRunMs("*/5 * * * *", TASHKENT, NOW),
    (error: unknown) =>
      error instanceof ReminderTimeError &&
      /fires more often than every 10 minutes/u.test(error.message),
  );
  assert.throws(
    () => nextCronRunMs("0 9 31 2 *", TASHKENT, NOW),
    (error: unknown) =>
      error instanceof ReminderTimeError &&
      /cron: never fires/u.test(error.message),
  );
  // croner отбивает шаг с числовым префиксом; текст обязан вести к форме, которую он берёт.
  assert.throws(
    () => nextCronRunMs("0/7 * * * *", TASHKENT, NOW),
    (error: unknown) =>
      error instanceof ReminderTimeError &&
      /stepping with numeric prefix[\s\S]*write the step as "\*\/7"/u.test(
        error.message,
      ),
  );
  assert.throws(
    () => nextCronRunMs("0 9 * * *", "Mars/Olympus", NOW),
    (error: unknown) =>
      error instanceof ReminderTimeError && /^cron:/u.test(error.message),
  );
});
