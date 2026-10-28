/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Свойства зонного времени напоминаний. Якоря контракта — в reminder-time.test.ts,
// здесь генератор перебирает то, что перечислением не закрыть: все дни и часы,
// шесть зон с переходами на летнее время, суммы относительных сроков.
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: при провале fast-check печатает строку вида
// `Property failed after N tests { seed: -1234567, path: "12:3:0", endOnFailure: true }`.
// Подставь её вторым аргументом — fc.assert(prop, { seed: -1234567, path: "12:3:0" }) —
// и прогон повторится байт в байт, включая shrink.
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import {
  REMINDER_MAX_HORIZON_MS,
  ReminderTimeError,
  resolveAt,
} from "./reminder-time.ts";
import { formatZoned } from "./zoned-time.ts";

const ZONES = [
  "UTC",
  "Asia/Tashkent",
  "Europe/Moscow",
  "America/New_York",
  "Europe/Berlin",
  "Australia/Sydney",
] as const;
const RUNS = { numRuns: 200 };
const pad = (value: number) => String(value).padStart(2, "0");

test("property: wall-clock roundtrip", () => {
  const now = Date.UTC(2026, 0, 1, 0, 0, 0);
  // Якорь на прошлое: генератор 2026+ с этим now задевает ветку почти никогда,
  // а отказ «в прошлом» - часть контракта, и он не должен держаться на удаче.
  assert.throws(
    () => resolveAt("2025-12-31 23:00", now, "Asia/Tashkent"),
    /is in the past/u,
  );
  fc.assert(
    fc.property(
      fc.integer({ min: 2026, max: 2027 }),
      fc.integer({ min: 1, max: 12 }),
      fc.integer({ min: 1, max: 28 }),
      fc.integer({ min: 0, max: 23 }),
      fc.integer({ min: 0, max: 59 }),
      fc.constantFrom(...ZONES),
      (y, m, d, hh, mm, tz) => {
        const input = `${y}-${pad(m)}-${pad(d)} ${pad(hh)}:${pad(mm)}`;
        let result: number;
        try {
          result = resolveAt(input, now, tz);
        } catch (error) {
          assert.ok(error instanceof ReminderTimeError);
          assert.match(
            error.message,
            /no such wall-clock time|in the past|more than a year ahead/u,
          );
          return;
        }
        assert.equal(formatZoned(result, tz), input);
        assert.equal(result % 60_000, 0);
        assert.ok(result > now, `${input} resolved into the past`);
      },
    ),
    RUNS,
  );
});

const UNIT = { d: 86_400_000, h: 3_600_000, m: 60_000, s: 1000 } as const;

test("property: relative durations never land in the past and add up", () => {
  const now = Date.UTC(2026, 8, 12, 5, 0, 0);
  fc.assert(
    fc.property(
      fc.array(
        fc.tuple(
          fc.integer({ min: 1, max: 500 }),
          fc.constantFrom("d", "h", "m", "s"),
        ),
        { minLength: 1, maxLength: 4 },
      ),
      fc.constantFrom(" ", "  ", "\t", " \t "),
      fc.constantFrom(...ZONES),
      (parts, gap, tz) => {
        const input = `in ${parts.map(([n, u]) => `${n}${u}`).join(gap)}`;
        const sum = parts.reduce((total, [n, u]) => total + n * UNIT[u], 0);
        if (sum > REMINDER_MAX_HORIZON_MS) {
          assert.throws(
            () => resolveAt(input, now, tz),
            /more than a year ahead/u,
          );
          return;
        }
        assert.equal(resolveAt(input, now, tz), now + sum);
      },
    ),
    RUNS,
  );
});
