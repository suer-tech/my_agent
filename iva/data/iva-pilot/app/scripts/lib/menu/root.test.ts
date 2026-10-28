/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registration promises. */
import test from "node:test";
import assert from "node:assert/strict";

import root from "./root.ts";

// Rich-кнопка живёт строкой в markdown: подпись и callback_data достаём из тега, порядок
// пар «кнопка — пояснение» и есть прежний порядок рядов.
const buttonsOf = (text: string): Array<[string, string]> =>
  [
    ...text.matchAll(
      /<tg-button[^>]*data="([^"]+)"[^>]*>([^<]*)<\/tg-button>/g,
    ),
  ].map((match) => [match[2], match[1]] as [string, string]);

function makeCtx(lang: string) {
  return {
    tr: (en: string, ru: string) => (lang === "ru" ? ru : en),
  };
}

const englishButtons = [
  ["🧠 Model", "iva_menu:mdl"],
  ["🤔 Thinking", "iva_menu:thk"],
  ["🔍 Search", "iva_menu:srch:o"],
  ["💬 Rich replies", "iva_menu:rich:o"],
  ["🎤 Voice", "iva_menu:voice:o"],
  ["🌐 Language", "iva_menu:lang:o"],
  ["🎭 Character", "iva_menu:chr:o"],
  ["💾 Memory", "iva_menu:core:o"],
  ["📡 Userbot", "iva_menu:ub:o"],
  ["🔗 Google", "iva_menu:gws:o"],
  ["⏰ Timers", "iva_menu:cron:o"],
  ["🔔 Notices", "iva_menu:ntc:o"],
  ["🧩 Skills", "iva_menu:sk:o"],
  ["📊 Status", "iva_menu:st:o"],
  ["🔀 New messages", "iva_menu:turn:o"],
  ["🛠 Maintenance", "iva_menu:svc:o"],
  ["✖ Close", "iva_menu:r:x"],
];

const russianButtons = [
  ["🧠 Модель", "iva_menu:mdl"],
  ["🤔 Размышления", "iva_menu:thk"],
  ["🔍 Поиск", "iva_menu:srch:o"],
  ["💬 Богатые ответы", "iva_menu:rich:o"],
  ["🎤 Голос", "iva_menu:voice:o"],
  ["🌐 Язык", "iva_menu:lang:o"],
  ["🎭 Характер", "iva_menu:chr:o"],
  ["💾 Память", "iva_menu:core:o"],
  ["📡 Userbot", "iva_menu:ub:o"],
  ["🔗 Google", "iva_menu:gws:o"],
  ["⏰ Кроны", "iva_menu:cron:o"],
  ["🔔 Уведомления", "iva_menu:ntc:o"],
  ["🧩 Скиллы", "iva_menu:sk:o"],
  ["📊 Статус", "iva_menu:st:o"],
  ["🔀 Новые сообщения", "iva_menu:turn:o"],
  ["🛠 Обслуживание", "iva_menu:svc:o"],
  ["✖ Закрыть", "iva_menu:r:x"],
];

test("root preserves English button order, callbacks, and close action", () => {
  const state = { page: 3 };
  const view = root.render(state, makeCtx("en"));

  assert.match(view.text, /^# ⚙️ Settings$/m);
  assert.deepEqual(buttonsOf(view.text), englishButtons);
  // Каждая кнопка — своя строка-абзац с пояснением «кнопка — что она делает».
  assert.equal(view.text.match(/<tg-button /g)?.length, englishButtons.length);
  assert.equal(
    view.text.match(/\n\n<tg-button /g)?.length,
    englishButtons.length,
  );
  assert.deepEqual(state, { page: 3 });
});

test("root translates labels without changing callback routing", () => {
  const view = root.render({}, makeCtx("ru"));

  assert.match(view.text, /^# ⚙️ Настройки$/m);
  assert.deepEqual(buttonsOf(view.text), russianButtons);
  assert.deepEqual(
    buttonsOf(view.text).map(([, callback]) => callback),
    englishButtons.map(([, callback]) => callback),
  );
});

test("root exposes the top-level screen contract", () => {
  assert.equal(root.parent, null);
  assert.equal(root.on("ignored", [], {}, makeCtx("en")), undefined);
});
