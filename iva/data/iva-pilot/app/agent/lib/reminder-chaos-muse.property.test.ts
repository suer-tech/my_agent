/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Хаос криворукого пользователя в датах напоминаний (прогон muse).
// Эмодзи вместо даты, опечатки, текст 50к, форвард с форварда, мусорные зоны,
// cron из шести полей, atMs = NaN/Infinity. Инварианты: дисциплина ошибок
// (только ReminderTimeError / ReminderStoreError наружу), границы успеха,
// идемпотентность нормализации cron.
//
// КАК ВОСПРОИЗВЕСТИ: seed в имени теста; при провале подставь и path:
// fc.assert(prop, { seed, path }).
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import {
  REMINDER_MAX_HORIZON_MS,
  ReminderTimeError,
  nextCronRunMs,
  resolveAt,
} from "./reminder-time.ts";
import { ReminderStoreError, normalizeSchedule } from "./reminder-store.ts";

const SEED = 20_260_913;
const NOW = Date.UTC(2026, 8, 12, 5, 0, 0);
const TZS = [
  "UTC",
  "Asia/Tashkent",
  "America/New_York",
  "Mars/Olympus",
  "",
  "X",
];

// Словарь опечаток и мусора живого пользователя.
const userDateArb = fc.constantFrom(
  "📅",
  "📅 завтра",
  "⏰",
  "завтра",
  "in 30 minut",
  "in 30 mimutes",
  "in -5m",
  "in 0m",
  "через полчаса",
  "14:30",
  "25:00",
  "99:99",
  "14-30",
  "2026-09-14 09:00",
  "2026-02-31 09:00",
  "2026-13-01 09:00",
  "0000-00-00 00:00",
  "2026-09-14T09:00",
  "2026-09-14T09:00:00+05:00",
  "",
  " ",
  "in 30m ".repeat(5000),
  "9".repeat(50_000),
  "in ",
  "in 1h 30m",
  "in 999999999999d",
  "/remind me tomorow",
  "[forwarded from Мама]\nнапомни в 15:00",
);

test(`resolveAt: мусор дает ReminderTimeError или срок в будущем (seed ${SEED})`, () => {
  fc.assert(
    fc.property(userDateArb, fc.constantFrom(...TZS), (input, tz) => {
      let result: number;
      try {
        result = resolveAt(input, NOW, tz);
      } catch (error) {
        // Прямой вызов с битой зоной — тот же ReminderTimeError, что и мусор дат:
        // никаких сырых RangeError наружу. (Боевой путь зону уже провалидировал:
        // ownerTimeZone сводит мусор к UTC через resolveTimeZone.)
        assert.ok(
          error instanceof ReminderTimeError,
          `не тот тип ошибки для ${JSON.stringify(input.slice(0, 40))}: ${String(error)}`,
        );
        assert.match(error.message, /^at: /u);
        return;
      }
      assert.ok(Number.isSafeInteger(result));
      assert.ok(result > NOW, "срок обязан быть в будущем");
      assert.ok(
        result <= NOW + REMINDER_MAX_HORIZON_MS,
        "срок в горизонте года",
      );
    }),
    { seed: SEED, numRuns: 300 },
  );
});

const cronArb = fc.oneof(
  fc.constantFrom(
    "0 9 * * 1-5",
    "*/5 * * * *",
    "0 0 31 2 *",
    "* * * * *",
    "0/7 * * * *",
    "0 9 * *",
    "0 9 * * * *",
    "каждый день в 9",
    "📅 9:00",
    "",
    "never",
    "0 9 * * 1-5 ".repeat(2000),
  ),
  fc
    .array(fc.string({ maxLength: 30 }), { minLength: 1, maxLength: 7 })
    .map((fields) => fields.join(" ")),
);

test(`nextCronRun: мусор дает ReminderTimeError или срок строго после (seed ${SEED})`, () => {
  fc.assert(
    fc.property(cronArb, fc.constantFrom(...TZS), (expr, tz) => {
      let result: number;
      try {
        result = nextCronRunMs(expr, tz, NOW);
      } catch (error) {
        assert.ok(
          error instanceof ReminderTimeError,
          `не тот тип ошибки для ${JSON.stringify(expr.slice(0, 40))}: ${String(error)}`,
        );
        assert.match(error.message, /^cron: /u);
        return;
      }
      assert.ok(Number.isSafeInteger(result));
      assert.ok(result > NOW);
    }),
    { seed: SEED, numRuns: 300 },
  );
});

test(`normalizeSchedule: мусор дает ReminderStoreError или идемпотентную норму (seed ${SEED})`, () => {
  const scheduleArb = fc.oneof(
    fc.constantFrom(null, 42, "cron", [], "at"),
    fc.record({
      kind: fc.constantFrom("at"),
      atMs: fc.oneof(
        fc.integer({ min: 0, max: Number.MAX_SAFE_INTEGER }),
        fc.constantFrom(-1, NaN, Infinity, 1.5, "завтра", null),
      ),
      extra: fc.constantFrom(undefined, 1),
    }),
    fc.record({
      kind: fc.constantFrom("cron"),
      expr: fc.oneof(cronArb, fc.constantFrom(42, null)),
      tz: fc.oneof(fc.constantFrom(...TZS), fc.constantFrom(7, null)),
      extra: fc.constantFrom(undefined, "x"),
    }),
  );
  fc.assert(
    fc.property(scheduleArb, (input) => {
      let first: ReturnType<typeof normalizeSchedule>;
      try {
        first = normalizeSchedule(input);
      } catch (error) {
        assert.ok(error instanceof ReminderStoreError);
        return;
      }
      assert.ok(first.kind === "at" || first.kind === "cron");
      // Нормализованное нормализуется в себя.
      assert.deepEqual(normalizeSchedule(first), first);
      if (first.kind === "cron") {
        assert.equal(first.expr, first.expr.trim().replace(/\s+/gu, " "));
        assert.equal(first.expr.split(" ").length, 5);
      }
    }),
    { seed: SEED, numRuns: 300 },
  );
});

// --- Примеры тупого пользователя: фиксированные входы ---

test("wall-clock с битой зоной — ReminderTimeError, а не сырой RangeError (F2)", () => {
  // Контракт ошибок единый: любой негодный вход — ReminderTimeError с префиксом «at:».
  assert.throws(
    () => resolveAt("25:00", NOW, ""),
    (error: unknown) =>
      error instanceof ReminderTimeError &&
      /^at: unknown time zone/u.test(error.message),
  );
  assert.throws(() => resolveAt("14:30", NOW, "Not/AZone"), ReminderTimeError);
  // Контроль: валидная зона и валидная форма по-прежнему дают срок.
  assert.ok(Number.isSafeInteger(resolveAt("14:30", NOW, "UTC")));
});

test("пример: эмодзи вместо даты - честный отказ, не молчание", () => {
  assert.throws(() => resolveAt("📅", NOW, "Asia/Tashkent"), ReminderTimeError);
  assert.throws(
    () => resolveAt("⏰ завтра в три", NOW, "Asia/Tashkent"),
    ReminderTimeError,
  );
});

test("пример: опечатка in 30 minut - отказ с подсказкой формы", () => {
  assert.throws(
    () => resolveAt("in 30 minut", NOW, "UTC"),
    /unsupported form/u,
  );
});

test("пример: форвард с форварда вставлен в дату - отказ", () => {
  assert.throws(
    () =>
      resolveAt("[forwarded from Мама]\nнапомни в 15:00", NOW, "Asia/Tashkent"),
    ReminderTimeError,
  );
});

test("пример: текст 50к знаков в дате - отказ быстро, не вис", () => {
  const started = Date.now();
  assert.throws(
    () => resolveAt("9".repeat(50_000), NOW, "UTC"),
    ReminderTimeError,
  );
  assert.ok(Date.now() - started < 2000, "отказ обязан быть быстрым");
});

test("пример: мусорная зона - ошибка расписания, не RangeError", () => {
  assert.throws(
    () =>
      normalizeSchedule({
        kind: "cron",
        expr: "0 9 * * *",
        tz: "Mars/Olympus",
      }),
    ReminderStoreError,
  );
});

test("пример: atMs NaN и Infinity - отказ стора", () => {
  for (const atMs of [NaN, Infinity, -Infinity, 1.5, -1]) {
    assert.throws(
      () => normalizeSchedule({ kind: "at", atMs }),
      ReminderStoreError,
      `atMs=${String(atMs)}`,
    );
  }
});

test("пример: cron из 6 полей и cron буквами - отказ", () => {
  assert.throws(
    () => normalizeSchedule({ kind: "cron", expr: "0 9 * * * *", tz: "UTC" }),
    /exactly 5 fields/u,
  );
  assert.throws(
    () => nextCronRunMs("каждый день в 9", "UTC", NOW),
    ReminderTimeError,
  );
});
