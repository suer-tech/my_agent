/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Хаос криворукого пользователя в карточках памяти (прогон muse).
// Файл вместо текста, фронтисерая строка, незакрытый фенс посреди истории,
// \r\n вперемешку, 50 тысяч строк, эмодзи в ключах. Цели - чистые splitCard,
// scanFences, hasUnclosedFence из agent/lib/card-text.ts.
// Инварианты: никогда не бросать на строке, длины outside совпадают,
// детерминизм, фронтисерая строка только если текст начинается с ---.
//
// КАК ВОСПРОИЗВЕСТИ: seed в имени теста; при провале подставь и path:
// fc.assert(prop, { seed, path }).
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import { hasUnclosedFence, scanFences, splitCard } from "./card-text.ts";
import { parseFrontmatter } from "./frontmatter.ts";

const SEED = 20_260_914;

const lineArb = fc.constantFrom(
  "---",
  "--- ",
  " ---",
  "```",
  "```js",
  "````",
  "~~~",
  " ~~~ python",
  "    ```",
  "# Заголовок",
  "## History",
  "status: active",
  "type: note",
  ":emoji: 📅 заголовок",
  "",
  " ",
  "обычная строка дневника",
  "- [ ] задача с [скобками]",
  "| таблица | труб |",
  "|---|---|",
  "текст с `инлайн-кодом` внутри",
  "```js `x`",
  "a".repeat(5000),
  "\t```",
  "```   ",
  "~~~\u200b",
);

const cardArb = fc.oneof(
  fc.array(lineArb, { maxLength: 40 }).map((lines) => lines.join("\n")),
  fc.array(lineArb, { maxLength: 40 }).map((lines) => lines.join("\r\n")),
  fc.array(lineArb, { maxLength: 40 }).map((lines) => lines.join("\r")),
  fc.constantFrom("", "\n", "---\n", "---\n---\n", "```"),
);

test(`splitCard: любой мусор делится без бросков (seed ${SEED})`, () => {
  fc.assert(
    fc.property(cardArb, (content) => {
      const { frontmatter, body } = splitCard(content);
      assert.equal(typeof body, "string");
      if (frontmatter !== null) {
        assert.equal(typeof frontmatter, "string");
        assert.ok(
          content
            .replace(/\r\n/g, "\n")
            .replace(/\r/g, "\n")
            .startsWith("---\n"),
          "фронтисерая строка только у текста с --- в начале",
        );
      }
      const again = splitCard(content);
      assert.deepEqual(again, { frontmatter, body });
    }),
    { seed: SEED, numRuns: 500 },
  );
});

test(`scanFences: outside ровно по строкам, детерминизм (seed ${SEED})`, () => {
  fc.assert(
    fc.property(fc.array(lineArb, { maxLength: 60 }), (lines) => {
      const scan = scanFences(lines);
      assert.equal(scan.outside.length, lines.length);
      assert.equal(typeof scan.open, "boolean");
      assert.deepEqual(scanFences(lines), scan);
      // Открытый фенс значит какая-то строка внутри.
      if (scan.open) assert.ok(scan.outside.includes(false));
    }),
    { seed: SEED, numRuns: 500 },
  );
});

test(`hasUnclosedFence: булево и согласовано со scanFences (seed ${SEED})`, () => {
  fc.assert(
    fc.property(cardArb, (content) => {
      const open = hasUnclosedFence(content);
      assert.equal(typeof open, "boolean");
      assert.equal(open, scanFences(content.split("\n")).open);
    }),
    { seed: SEED, numRuns: 500 },
  );
});

// --- Примеры тупого пользователя: фиксированные входы ---

test("горизонтальная черта в начале не съедает голову карточки (F3)", () => {
  const content = "---\nКакой-то текст\n---\nНастоящее тело";
  const { frontmatter, body } = splitCard(content);
  // Блок без ни одной строки-ключа — не метаданные: голова остаётся в теле,
  // и UPDATE через write_card её не теряет.
  assert.equal(frontmatter, null);
  assert.equal(body, content);
  assert.equal(parseFrontmatter(content).body, content);
  // Контроль: настоящий frontmatter с ключом по-прежнему метаданные.
  const real = splitCard("---\ntype: note\n---\nтело");
  assert.equal(real.frontmatter, "type: note");
  assert.equal(real.body, "тело");
});

test("пример: незакрытый фенс прячет заголовки History", () => {
  const body = "начало\n```js\nкод без конца\n## History\n- событие";
  assert.equal(hasUnclosedFence(body), true);
  const scan = scanFences(body.split("\n"));
  assert.deepEqual(scan.outside, [true, false, false, false, false]);
});

test("пример: четыре бэктика не закрываются тремя - остаток в коде", () => {
  const body = "````\nкод\n```\nещё код";
  assert.equal(hasUnclosedFence(body), true);
});

test("пример: строка ```js с бэктиком - не фенс, а текст", () => {
  const scan = scanFences(["```js `x`", "## History"]);
  assert.deepEqual(scan.outside, [true, true]);
});

test("пример: карточка 50 тысяч строк - разбор быстрый", () => {
  const content = `---\nstatus: active\n---\n${"строка\n".repeat(50_000)}\`\`\`\nхвост`;
  const started = Date.now();
  const { frontmatter, body } = splitCard(content);
  const open = hasUnclosedFence(body);
  assert.equal(frontmatter, "status: active");
  assert.equal(open, true);
  assert.ok(Date.now() - started < 2000, "разбор обязан быть быстрым");
});
