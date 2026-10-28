// Шумовые свойства ночного сжатия CORE: мусорные секции, гигантские строки, CRLF,
// указатели. Контракт clampCore («детерминированно сжимает, не режет заголовки,
// указатели и незнакомые секции») проверяется на повторном применении.
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: при провале fast-check печатает строку вида
// `Property failed after N tests { seed: -1234567, path: "12:3:0", endOnFailure: true }`.
// Подставь её вторым аргументом — fc.assert(prop, { seed: -1234567, path: "12:3:0" }) —
// и прогон повторится байт в байт, включая shrink.
import test from "node:test";
import assert from "node:assert/strict";
import fc from "fast-check";
import { CORE_CAP } from "./core-cap.ts";
import { clampCore } from "./core-clamp.ts";

const POINTER = "- Последний день: summaries/daily/2026-09-12 · Индекс: MOC.md";

const coreLike = fc
  .array(
    fc.oneof(
      { weight: 1, arbitrary: fc.constant("## Пользователь") },
      { weight: 2, arbitrary: fc.constant("## Предпочтения") },
      { weight: 1, arbitrary: fc.constant("## Активные цели (≤3)") },
      { weight: 2, arbitrary: fc.constant("## Указатели") },
      { weight: 1, arbitrary: fc.constant(POINTER) },
      { weight: 3, arbitrary: fc.constant("## Незнакомая секция") },
      {
        weight: 8,
        arbitrary: fc
          .string({ maxLength: 400 })
          .map((body) => `- ${body || "пусто"}`),
      },
    ),
    { minLength: 4, maxLength: 80 },
  )
  .map((lines) => lines.join("\n"));

await test("clampCore идемпотентен: повторное сжатие не съедает ещё строки", () => {
  fc.assert(
    fc.property(coreLike, (text) => {
      if (text.length <= CORE_CAP) return; // этот случай контракт пропускает как есть
      const once = clampCore(text);
      const twice = clampCore(once);
      assert.equal(
        twice,
        once,
        `повторный clampCore изменил результат: ${once.length} -> ${twice.length}`,
      );
    }),
    { seed: 20_260_916, numRuns: 300 },
  );
});

await test("сжатие не теряет заголовки секций и строку указателя", () => {
  fc.assert(
    fc.property(coreLike, (text) => {
      const out = clampCore(text);
      for (const heading of text.match(/^##[ \t]+.+$/gmu) ?? [])
        assert.ok(out.includes(heading), `потерян заголовок ${heading}`);
      if (text.includes(POINTER))
        assert.ok(
          out.includes(POINTER),
          "потеряна строка указателя последнего дня",
        );
    }),
    { seed: 20_260_917, numRuns: 200 },
  );
});

// Ночное сжатие зовётся на каждом ходу, когда CORE больше потолка
// (agent/instructions/20-core.ts), и на ночном прогоне. Поэтому стоимость обязана
// расти линейно: 4× вход не может давать кратный рост времени.
const coreWithBullets = (count: number, pad: number) =>
  "## Предпочтения\n" +
  Array.from(
    { length: count },
    (_, index) => `- 2026-01-01 ${"x".repeat(pad)} ${index}`,
  ).join("\n") +
  "\n## Указатели\n- Последний день: summaries/daily/2026-09-12 · Индекс: MOC.md\n";

function bestOf(runs: number, fn: () => void): number {
  let best = Number.POSITIVE_INFINITY;
  for (let i = 0; i < runs; i++) {
    const started = performance.now();
    fn();
    best = Math.min(best, performance.now() - started);
  }
  return best;
}

await test("clampCore растёт линейно от размера CORE, а не квадратично (seed 20260919)", () => {
  fc.assert(
    fc.property(fc.integer({ min: 150, max: 250 }), (pad) => {
      const small = coreWithBullets(1_000, pad);
      const large = coreWithBullets(4_000, pad);
      const tSmall = bestOf(3, () => void clampCore(small));
      const tLarge = bestOf(3, () => void clampCore(large));
      assert.ok(
        tLarge <= tSmall * 8 + 20,
        `4× вход (${small.length} -> ${large.length} знаков) дал ${(tLarge / tSmall).toFixed(1)}× времени ` +
          `(${tSmall.toFixed(0)} -> ${tLarge.toFixed(0)} мс) — сжатие близко к O(n²)`,
      );
    }),
    { seed: 20_260_919, numRuns: 3 },
  );
});
