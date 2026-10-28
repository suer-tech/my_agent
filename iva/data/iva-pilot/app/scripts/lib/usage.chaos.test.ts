// Мусорные и гигантские числа расхода от провайдера: хук → data/usage.jsonl → отчёт /usage.
// Договор починки: число расхода — конечное неотрицательное целое; всё остальное —
// пропуск шага со строкой в журнале, а отчёт не печатает Infinity даже по старому логу.
//
// ЧТО ЛОМАЕТСЯ У ПОЛЬЗОВАТЕЛЯ. Провайдер вернул в usage валидное JSON-число вроде 1e308
// (биллинговый баг, чужой прокси с испорченным счётчиком) — и учёт расхода врёт молча:
// `total` шага не влезает в double и уезжает в лог как `null`, а /usage и `iva usage`
// показывают «Today: 0 tokens (in Infinity / out Infinity)». Строка лога остаётся в файле
// навсегда, поэтому следующая подрезка не поможет: числа не сходятся ни в одном окне.
// Проверок на стороне записи нет вовсе (`u.inputTokens ?? 0` в agent/hooks/usage.ts), а
// читатель складывает как есть — конечное на записи становится бесконечным при сложении.
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: seed в имени теста; при провале подставь ещё и path:
// fc.assert(prop, { seed: SEED, path }).
await import("./ts-esm-hooks.ts");
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import fc from "fast-check";
import { formatUsageReport, readEntries, summarize } from "./usage.ts";

const SEED = 20_260_917;
const HOOK = (await import("../../agent/hooks/usage.ts")).default;
// eve типизирует события хука опциональными; шаг расхода здесь заведомо есть.
type StepHandler = (event: unknown, ctx: unknown) => void;
const STEP_COMPLETED = (HOOK as { events?: Record<string, unknown> }).events?.[
  "step.completed"
] as StepHandler;
const CTX = { session: { id: "s1" }, channel: { kind: "channel:telegram" } };

type Usage = {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
};

function step(usage: Usage, index: number): void {
  STEP_COMPLETED({ data: { stepIndex: index, turnId: "turn_1", usage } }, CTX);
}

/** Одно окно: хук пишет в свой каталог данных, отчёт читает из него же. */
function withDir<T>(run: (dir: string) => T): T {
  const dir = mkdtempSync(join(tmpdir(), "iva-usage-chaos-"));
  const previous = process.env.ASSISTANT_DATA_DIR;
  process.env.ASSISTANT_DATA_DIR = dir;
  try {
    return run(dir);
  } finally {
    if (previous === undefined) delete process.env.ASSISTANT_DATA_DIR;
    else process.env.ASSISTANT_DATA_DIR = previous;
    rmSync(dir, { recursive: true, force: true });
  }
}

const NUMBERS = fc.oneof(
  fc.integer({ min: 0, max: 10_000 }),
  fc.constantFrom(0.5, -3, 1e12, 1e15, 1e100, 1e300, 1e308, -1e308),
);

const USAGE = fc.record({
  inputTokens: NUMBERS,
  outputTokens: NUMBERS,
  cacheReadTokens: NUMBERS,
  cacheWriteTokens: NUMBERS,
});

await test("лог и отчёт не хранят нечисло ни при каком расходе провайдера (seed 20260917)", () => {
  fc.assert(
    fc.property(fc.array(USAGE, { minLength: 1, maxLength: 3 }), (steps) => {
      withDir((dir) => {
        steps.forEach((usage, index) => step(usage, index));
        for (const entry of readEntries(dir)) {
          for (const field of [
            "in",
            "out",
            "cacheRead",
            "cacheWrite",
            "total",
          ] as const) {
            assert.ok(
              Number.isFinite(entry[field]),
              `лог хранит не-число: ${field}=${String(entry[field])} при usage=${JSON.stringify(steps)}`,
            );
          }
        }
        const report = formatUsageReport(
          summarize(readEntries(dir), { window: "today", now: Date.now() }),
        );
        assert.ok(
          !/Infinity|NaN/u.test(report),
          `отчёт показывает не-число при usage=${JSON.stringify(steps)}:\n${report}`,
        );
      });
    }),
    { seed: SEED, numRuns: 200 },
  );
});

await test("минимальный контрпример: 1e308 в usage пропускается с журналом, а не в null и Infinity (seed 20260917)", () => {
  withDir((dir) => {
    const written: string[] = [];
    const original = console.error;
    console.error = (...args: unknown[]) =>
      void written.push(args.map((arg) => String(arg)).join(" "));
    try {
      step(
        {
          inputTokens: 1e308,
          outputTokens: 1e308,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
        },
        0,
      );
      // Строка с неисполнимым расходом в лог не идёт вовсе: сумма такого числа теряет
      // конечность, а `JSON.stringify` пишет Infinity как null.
      assert.deepEqual(readEntries(dir), [], "мусорный расход записан в лог");
      assert.ok(
        written.some((line) => line.includes("usage")),
        "пропуск обязан оставить строку в журнале, а не молчать",
      );

      // Исполнимое большое число проходит как обычно — контроль, что режем не «всё большое».
      step(
        {
          inputTokens: 1_000_000_000,
          outputTokens: 1_000_000_000,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
        },
        1,
      );
      const entries = readEntries(dir);
      assert.equal(entries.length, 1);
      assert.equal(entries[0]?.total, 2_000_000_000);

      // И отчёт по такому логу не печатает ничего нечислового.
      const report = formatUsageReport(
        summarize(readEntries(dir), { window: "today", now: Date.now() }),
      );
      assert.ok(
        !/Infinity|NaN/u.test(report),
        `пользователь видит в /usage:\n${report}`,
      );
      assert.match(report, /2 000 000 000 tokens/u);
    } finally {
      console.error = original;
    }
  });
});

await test("минимальный контрпример: отрицательный расход шага пропускается с журналом (seed 20260917)", () => {
  withDir((dir) => {
    const written: string[] = [];
    const original = console.error;
    console.error = (...args: unknown[]) =>
      void written.push(args.map((arg) => String(arg)).join(" "));
    try {
      step(
        {
          inputTokens: -1,
          outputTokens: 0,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
        },
        0,
      );
      // Отрицательное число — мусор провайдера: сумма с ним теряет смысл, а окно last
      // берёт in в обход sum, поэтому строка не должна попасть в лог вовсе.
      assert.deepEqual(
        readEntries(dir),
        [],
        "отрицательный расход записан в лог",
      );
      assert.ok(
        written.some((line) => line.includes("usage")),
        "пропуск обязан оставить строку в журнале, а не молчать",
      );

      // Ноль и положительное число проходят как обычно — режем знак, а не всё подряд.
      step(
        {
          inputTokens: 1,
          outputTokens: 0,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
        },
        1,
      );
      assert.equal(readEntries(dir).length, 1);
    } finally {
      console.error = original;
    }
  });
});

await test("старый лог с гигантскими числами не печатает Infinity (seed 20260917)", () => {
  withDir((dir) => {
    // Лог, записанный до починки: строка с 1e308 и total:null уже лежит на диске.
    writeFileSync(
      join(dir, "usage.jsonl"),
      [
        JSON.stringify({
          ts: new Date().toISOString(),
          source: "channel:telegram",
          provider: "ollama",
          model: "deepseek-v4-pro",
          sessionId: "s1",
          turnId: "turn_1",
          step: 0,
          in: 1e308,
          out: 1e308,
          cacheRead: 0,
          cacheWrite: 0,
          total: null,
        }),
        JSON.stringify({
          ts: new Date().toISOString(),
          source: "channel:telegram",
          provider: "ollama",
          model: "deepseek-v4-pro",
          sessionId: "s1",
          turnId: "turn_1",
          step: 1,
          in: 1e308,
          out: 1e308,
          cacheRead: 0,
          cacheWrite: 0,
          total: null,
        }),
        "",
      ].join("\n"),
    );
    const report = formatUsageReport(
      summarize(readEntries(dir), { window: "today", now: Date.now() }),
    );
    assert.ok(
      !/Infinity|NaN/u.test(report),
      `старый лог печатает нечисло:\n${report}`,
    );
  });
});

// T34-C: одиночное 1e308 из старого лога проходило `Number.isFinite` и доезжало до отчёта
// гигантским числом (двух таких хватало на Infinity и потолок, одного - нет). Потолок должен
// быть тот же, что у писателя (agent/hooks/usage.ts:35): расход - безопасное целое, всё прочее
// из старого лога считается нулём, а не числом.
await test("одиночное 1e308 из старого лога не доезжает до отчёта (seed 20260918)", () => {
  withDir((dir) => {
    writeFileSync(
      join(dir, "usage.jsonl"),
      JSON.stringify({
        ts: new Date().toISOString(),
        source: "channel:telegram",
        provider: "ollama",
        model: "deepseek-v4-pro",
        sessionId: "s1",
        turnId: "turn_1",
        step: 0,
        in: 1e308,
        out: 1e308,
        cacheRead: 1e308,
        cacheWrite: 1e308,
        total: 1e308,
      }) + "\n",
    );
    const summary = summarize(readEntries(dir), {
      window: "today",
      now: Date.now(),
    });
    assert.equal(summary.totals.in, 0, "1e308 - мусор, а не расход");
    assert.equal(summary.totals.total, 0);
    const report = formatUsageReport(summary);
    assert.doesNotMatch(report, /e\+\d|1e308|Infinity|NaN/u, report);
  });
});
