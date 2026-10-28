/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Зонная арифметика: якорь формата и свойство переноса. formatZoned и zonedToUtcMs —
// обратные друг другу с точностью до минуты в любой зоне: стенное время, снятое с момента,
// возвращается тем же моментом и печатается той же строкой.
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: fast-check печатает `{ seed: …, path: "…" }`; подставь их
// вторым аргументом fc.assert, и прогон повторится байт в байт, включая shrink.
import test from "node:test";
import assert from "node:assert/strict";
import fc from "fast-check";
import { formatZoned, zonedParts, zonedToUtcMs } from "./zoned-time.ts";

const ZONED_TIME_SEED = 20260912;
const ZONES = [
  "UTC",
  "Asia/Tashkent",
  "Europe/Berlin",
  "America/New_York",
  "Australia/Lord_Howe", // сдвиг на 30 минут: не только целые часы
];

const pad = (value: number) => String(value).padStart(2, "0");

test("formatZoned дополняет месяц, день, часы и минуты нулями", () => {
  assert.equal(
    formatZoned(
      zonedToUtcMs(2026, 9, 12, 9, 5, "Asia/Tashkent"),
      "Asia/Tashkent",
    ),
    "2026-09-12 09:05",
  );
  // Полночь в en-US форматируется как 24 — zonedParts сводит её к 0.
  assert.equal(
    formatZoned(zonedToUtcMs(2026, 1, 2, 0, 0, "UTC"), "UTC"),
    "2026-01-02 00:00",
  );
});

test(`formatZoned и zonedToUtcMs сходятся с точностью до минуты (seed ${ZONED_TIME_SEED})`, () => {
  fc.assert(
    fc.property(
      fc.integer({ min: Date.UTC(2020, 0, 1), max: Date.UTC(2035, 11, 31) }),
      fc.constantFrom(...ZONES),
      (epochMs, tz) => {
        const { y, m, d, hh, mm } = zonedParts(epochMs, tz);
        const expected = `${y}-${pad(m)}-${pad(d)} ${pad(hh)}:${pad(mm)}`;
        const placed = zonedToUtcMs(y, m, d, hh, mm, tz);
        assert.equal(formatZoned(placed, tz), expected);
        assert.match(
          formatZoned(placed, tz),
          /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/u,
        );
      },
    ),
    { seed: ZONED_TIME_SEED, numRuns: 300 },
  );
});
