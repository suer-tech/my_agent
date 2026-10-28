// Хаос-прогон тула tasks и tasks.json: мусор в файле, гонки двух писателей.
// Найдено 2026-09-13 маршрутом pbt/deepseek-4-3 (раунд 3).
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: seed стоит в имени свойства; при провале fast-check печатает
// `{ seed, path, endOnFailure: true }`, который повторяет прогон байт в байт.
//
// КРАСНЫЙ тест здесь - находка; продакшн-код не менялся, починка описана в отчёте
// `.scratch/work/reviews/pbt-deepseek-4-3-2026-09-12.md`.

import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import fc from "fast-check";

// Хук резолвинга идёт первым: тулы тянут соседей NodeNext-спецификаторами.
import "./lib/ts-esm-hooks.ts";

const dataDir = mkdtempSync(join(tmpdir(), "pbt-r3-tasks-"));
process.on("exit", () => rmSync(dataDir, { recursive: true, force: true }));
process.env.ASSISTANT_DATA_DIR = dataDir;

const { default: tasksTool } = await import("../agent/tools/tasks.ts");

const SEED = 20_260_913;
const FILE = join(dataDir, "tasks.json");

type TaskRow = {
  id?: unknown;
  text?: unknown;
  priority?: unknown;
  due?: unknown;
  done?: unknown;
  createdAt?: unknown;
  removedAt?: unknown;
};
type Answer = {
  ok?: boolean;
  error?: string;
  count?: number;
  total?: number;
  tasks?: TaskRow[];
  added?: TaskRow;
  done?: TaskRow;
  removed?: TaskRow;
};

const call = async (input: {
  action: "add" | "list" | "done" | "remove";
  text?: string;
  id?: number;
  includeDone?: boolean;
}): Promise<Answer> => (await tasksTool.execute(input, {} as never)) as Answer;

const readRows = (): TaskRow[] => {
  const parsed: unknown = JSON.parse(readFileSync(FILE, "utf8"));
  return Array.isArray(parsed) ? (parsed as TaskRow[]) : [];
};
const isPositiveInt = (value: unknown): boolean =>
  typeof value === "number" && Number.isInteger(value) && value > 0;

function reset(contents = "[]"): void {
  writeFileSync(FILE, contents);
}

// НАХОДКА R3-4. Файл задач не проверяется по форме: одна мусорная запись ломает
// арифметику id или роняет разбор. Самый тихий случай: `[{"id":"x"}]` даёт
// `Math.max(0, "x") + 1 = NaN`, JSON.stringify пишет `"id": null`, и все новые задачи
// навсегда получают id: null — их нельзя ни закрыть, ни удалить (схема тула требует
// положительный целый id). Другие формы дают модели сырые TypeError вместо диагноза.
await test("НАХОДКА R3-4: мусорная запись не превращает новые задачи в id: null", async () => {
  reset('[{"id":"x","text":"junk","done":false}]');
  const answer = await call({ action: "add", text: "новая" });
  assert.ok(
    answer.ok !== true ||
      (isPositiveInt(answer.added?.id) && isPositiveInt(answer.added?.id)),
    `новая задача получила id ${JSON.stringify(answer.added?.id)}`,
  );
});

await test("НАХОДКА R3-4: чужой корень файла даёт диагноз, а не TypeError", async () => {
  reset('{"a":1}');
  const answer = await call({ action: "add", text: "новая" });
  assert.ok(
    answer.ok !== true,
    "add на объекте вместо массива не должен удаваться",
  );
  assert.doesNotMatch(
    answer.error ?? "",
    /is not a function|Cannot read properties/u,
    `сырая ошибка движка: ${answer.error}`,
  );
});

await test("НАХОДКА R3-4: пропущенные записи называют себя в журнале", async (t) => {
  reset(
    '[null,{"id":"x","text":"junk","done":false},' +
      '{"id":7,"text":"живая","priority":"med","due":null,"done":false,"createdAt":"2026-09-12T00:00:00.000Z"}]',
  );
  const lines: string[] = [];
  t.mock.method(console, "warn", (...args: unknown[]) => {
    lines.push(args.map(String).join(" "));
  });
  const answer = await call({ action: "list" });
  assert.equal(answer.ok, true, JSON.stringify(answer));
  assert.equal(answer.count, 1, JSON.stringify(answer));
  assert.ok(
    lines.some((line) => line.includes("tasks.json") && line.includes("2")),
    JSON.stringify(lines),
  );
});

await test(`НАХОДКА R3-4: свойство «мусорный tasks.json не ломает пространство id» (seed ${SEED})`, async () => {
  const junkFile = fc.constantFrom(
    "[null]",
    "[42]",
    '["x"]',
    '{"a":1}',
    '[{"id":"x","text":"junk","done":false}]',
    '[{"id":null,"text":"junk","done":false}]',
    '[{"id":1.5,"text":"junk","done":false}]',
    '[{"id":-3,"text":"junk","done":false}]',
    '[{"text":"junk"}]',
    "[[1]]",
    "[true]",
  );
  await fc.assert(
    fc.asyncProperty(junkFile, async (raw) => {
      reset(raw);
      const answer = await call({ action: "add", text: "новая" });
      if (answer.ok !== true) {
        assert.doesNotMatch(
          answer.error ?? "",
          /is not a function|Cannot read properties/u,
          `${raw} -> ${answer.error}`,
        );
        return;
      }
      assert.ok(
        isPositiveInt(answer.added?.id),
        `${raw} -> новая задача получила id ${JSON.stringify(answer.added?.id)}`,
      );
      for (const row of readRows())
        assert.ok(
          isPositiveInt(row.id),
          `${raw} -> запись с id ${JSON.stringify(row.id)}`,
        );
    }),
    { seed: SEED, numRuns: 22 },
  );
});

// Зелёные controls: нормальные гонки и обычные отказы держатся.
await test("зелёное: одновременные add не теряются и не дублируют id", async () => {
  reset();
  const answers = await Promise.all([
    call({ action: "add", text: "one" }),
    call({ action: "add", text: "two" }),
    call({ action: "add", text: "three" }),
  ]);
  const ids = answers
    .map((answer) => answer.added?.id)
    .filter(isPositiveInt)
    .sort((a, b) => (a as number) - (b as number));
  assert.deepEqual(ids, [1, 2, 3], JSON.stringify(answers));
  assert.equal(readRows().length, 3);
});

await test("зелёное: случайный поток add/done/remove из двух писателей держит инварианты", async () => {
  let state = 0x5eed;
  const rnd = (): number => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
  reset();
  const added = new Set<string>();
  const removedTexts = new Set<string>();
  let next = 0;
  for (let round = 0; round < 30; round++) {
    const text = `task-${next++}`;
    const operations: Promise<Answer>[] = [
      call({ action: "add", text }),
      call({ action: "add", text: `${text}-b` }),
    ];
    if (round % 3 === 0) operations.push(call({ action: "list" }));
    const answers = await Promise.all(operations);
    for (const answer of answers.slice(0, 2)) {
      assert.ok(answer.ok, JSON.stringify(answer));
      added.add(String(answer.added?.text));
    }
    const rows = readRows();
    const ids = rows.map((row) => row.id);
    assert.equal(
      new Set(ids).size,
      ids.length,
      `дубли id: ${JSON.stringify(ids)}`,
    );
    for (const row of rows) {
      assert.ok(isPositiveInt(row.id), JSON.stringify(row));
      assert.equal(typeof row.text, "string");
      if (rnd() < 0.3) {
        const action = rnd() < 0.5 ? "done" : "remove";
        const outcome = await call({ action, id: row.id as number });
        if (row.removedAt !== undefined && action === "done") {
          assert.equal(outcome.ok, false);
          assert.equal(outcome.error, `Задача ${String(row.id)} удалена`);
        } else assert.ok(outcome.ok, JSON.stringify(outcome));
        if (outcome.removed) removedTexts.add(String(outcome.removed.text));
      }
    }
  }
  const finalTexts = new Set(readRows().map((row) => String(row.text)));
  for (const text of added)
    assert.ok(
      finalTexts.has(text) || removedTexts.has(text),
      `потеряна задача ${text}`,
    );
});

await test("зелёное: битый JSON — понятный отказ и бэкап, файл не перетёрт", async () => {
  reset("{broken");
  const answer = await call({ action: "add", text: "новая" });
  assert.ok(answer.ok !== true);
  assert.match(answer.error ?? "", /damaged \(invalid JSON\)/u);
  const backups = readdirSync(dataDir).filter((name) =>
    name.startsWith("tasks.json.corrupt-"),
  );
  assert.equal(backups.length, 1);
  assert.equal(
    readFileSync(join(dataDir, backups[0]), "utf8"),
    "{broken",
    "бэкап обязан содержать исходный файл",
  );
  assert.equal(readdirSync(dataDir).includes("tasks.json"), false);
});
