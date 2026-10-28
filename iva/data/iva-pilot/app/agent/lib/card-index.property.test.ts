// Одна карточка вольта не имеет права выключить каталог. Найдено сценарным поиском
// (pbt-opus №1, независимо подтверждено Muse F6/F6b), починено обёрткой
// parseFrontmatterOrSkip.
//
// Что ломалось для пользователя: одна карточка вольта, написанная человеком руками,
// с кавычкой во frontmatter (`company: 'Sayyora's Splendor'`) — и Ива перестаёт
// искать по памяти ВООБЩЕ. Не «эта карточка не находится», а весь memory_search
// падал FrontmatterParseError: cardIndex звали вне try/catch, который прикрывает
// только чтение файла (agent/tools/memory_search.ts). Тот же вызов без прикрытия
// был в agent/lib/card-store.ts (разбор [[ссылок]]), в scripts/memory/embed-index.ts
// (ночной индекс эмбеддингов) и в agent/tools/write_card.ts (storedStatus).
//
// КАК ВОСПРОИЗВЕСТИ: при провале fast-check печатает
// `{ seed: 20260912, path: "1:4:4", endOnFailure: true }` — подставь это вторым
// аргументом fc.assert(prop, { seed, path }), прогон повторится байт в байт.
import test from "node:test";
import assert from "node:assert/strict";
import fc from "fast-check";
import { cardIndex } from "./card-index.ts";

const SEED = 20_260_912;
const RUNS = 3000;

// Значения, какие человек печатает в Obsidian: имена с апострофом, кавычки, двоеточия,
// пустые поля. Ничего экзотического — ни управляющих символов, ни юникод-мусора.
const value = fc.oneof(
  fc.constantFrom(
    "Мария Лепёхина",
    "'Sayyora's Splendor'",
    "O'Brien",
    "'Мария",
    '"Kaspersky',
    "co-founder",
    "",
    "  ",
    "a: b",
    "#hash",
    "- дефис",
  ),
  fc.string({ maxLength: 16 }),
);
const key = fc.constantFrom(
  "name",
  "company",
  "role",
  "aliases",
  "tags",
  "title",
);
const card = fc
  .array(fc.tuple(key, value), { minLength: 1, maxLength: 5 })
  .map(
    (pairs) =>
      `---\n${pairs.map(([k, v]) => `${k}: ${v}`).join("\n")}\n---\n\nтело карточки\n`,
  );

await test(`любая карточка, написанная руками, индексируется без исключения (seed ${SEED})`, () => {
  fc.assert(
    fc.property(card, (text) => {
      assert.doesNotThrow(
        () => cardIndex(text, "cards/проверка.md"),
        `карточка роняет индекс целиком: ${JSON.stringify(text)}`,
      );
    }),
    { seed: SEED, numRuns: RUNS },
  );
});

// Минимальный контрпример из прогона выше, зафиксированный отдельно: он должен
// оставаться читаемым и после того, как генератор поменяется.
await test("минимальный контрпример: апостроф внутри кавычек", () => {
  const journal: string[] = [];
  const indexed = cardIndex(
    "---\ncompany: 'Sayyora's Splendor'\n---\n\nтело\n",
    "cards/contacts/sayyora.md",
    (line) => journal.push(line),
  );
  assert.equal(indexed, null, "битая карточка не индексируется");
  assert.equal(journal.length, 1, "и не пропадает молча");
  assert.match(journal[0], /cards\/contacts\/sayyora\.md/);
});

await test("минимальный контрпример: незакрытая кавычка в имени", () => {
  assert.equal(
    cardIndex("---\nname: 'Мария\n---\n\nтело\n", "cards/maria.md", () => {}),
    null,
  );
});

await test("здоровая карточка по-прежнему разбирается полностью", () => {
  const indexed = cardIndex(
    "---\nname: Батыр\ncompany: Majento\n---\n\nвстреча про бюджет\n",
    "cards/contacts/batyr.md",
  );
  assert.equal(indexed?.fm.name, "Батыр");
  assert.equal(indexed?.meta, "Батыр Majento");
  assert.match(String(indexed?.body), /встреча про бюджет/);
});
