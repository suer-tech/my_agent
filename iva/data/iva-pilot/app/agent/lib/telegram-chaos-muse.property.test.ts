/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Хаос криворукого пользователя на входе Telegram (прогон muse).
// Чистые функции telegram-parts.ts: сырой апдейт может нести что угодно -
// файл вместо текста, форвард с форварда, дубли, эмодзи-ключи, 50 тысяч знаков.
// Инварианты: никогда не бросать, формы держать, повтор детерминирован.
//
// КАК ВОСПРОИЗВЕСТИ: seed напечатан в имени каждого теста; при провале fast-check
// допечатает path - подставь оба: fc.assert(prop, { seed, path }).
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import {
  contentKeyNames,
  mediaFromRaw,
  messageParts,
  type TelegramRawMessage,
} from "./telegram-parts.ts";

const SEED = 20_260_912;

// Ключи: настоящие поля Bot API, мусор, эмодзи, пробелы, 50-тысячные простыни.
const keyArb = fc.constantFrom(
  "message_id",
  "chat",
  "from",
  "text",
  "caption",
  "photo",
  "document",
  "voice",
  "video",
  "forward_origin",
  "reply_to_message",
  "iva_parts",
  "media_group_id",
  "location",
  "contact",
  "poll",
  "sticker",
  "emoji-ключ",
  "ключ с пробелом",
  "📅",
  "x".repeat(200),
  "__proto__",
  "constructor",
);

// Значения: скаляры, вложенность, массивы, юникод-мусор, длинные строки.
const scalarArb = fc.constantFrom(
  null,
  0,
  -1,
  42,
  3.14,
  "",
  " ",
  "привет",
  "📅⏰🎉",
  "a".repeat(50_000),
  "\u0000\u0007\u200b\ufb01",
  "lone surrogate \ud800 хвост",
  true,
  false,
);

function jsonValue(depth: number): fc.Arbitrary<unknown> {
  if (depth <= 0) return scalarArb;
  const nested = jsonValue(depth - 1);
  return fc.oneof(
    scalarArb,
    fc.array(fc.tuple(keyArb, nested), {
      maxLength: 4,
    }),
  );
}

function recordArb(depth: number): fc.Arbitrary<TelegramRawMessage> {
  const nested = jsonValue(depth);
  return fc
    .array(fc.tuple(keyArb, nested), {
      maxLength: 6,
    })
    .map((entries) => {
      const out: TelegramRawMessage = {};
      for (const [k, v] of entries) {
        if (k === "__proto__" || k === "constructor") continue;
        if (Array.isArray(v)) {
          const rec: TelegramRawMessage = {};
          for (const [kk, vv] of v as Array<[string, unknown]>) {
            if (kk === "__proto__" || kk === "constructor") continue;
            rec[kk] = vv;
          }
          out[k] = rec;
        } else {
          out[k] = v;
        }
      }
      return out;
    });
}

// Фото-массив криворукого: битые элементы, file_id не строкой, пустые.
const photoArb = fc.array(
  fc.oneof(
    fc.constantFrom(null, 42, "строка", [], {}),
    fc.record({
      file_id: fc.oneof(
        fc.string({ maxLength: 40 }),
        fc.constantFrom(42, null),
      ),
      file_unique_id: fc.oneof(
        fc.string({ maxLength: 20 }),
        fc.constantFrom(1),
      ),
    }),
  ),
  { maxLength: 5 },
);

test(`parts: мусорный raw не роняет разбор (seed ${SEED})`, () => {
  fc.assert(
    fc.property(recordArb(2), (raw) => {
      const parts = messageParts(raw);
      assert.ok(Array.isArray(parts));
      assert.ok(parts.length >= 1);
      for (const part of parts) {
        assert.equal(typeof part, "object");
        assert.ok(part !== null && !Array.isArray(part));
      }
      // Детерминизм: тот же вход - тот же выход.
      assert.deepEqual(messageParts(raw), parts);
    }),
    { seed: SEED, numRuns: 300 },
  );
});

test(`media: битые photo и file_id не строкой дают null или форму (seed ${SEED})`, () => {
  fc.assert(
    fc.property(
      photoArb,
      fc.string({ maxLength: 50_000 }),
      (photo, fileName) => {
        const raw: TelegramRawMessage = {
          photo,
          document: { file_name: fileName },
        };
        const media = mediaFromRaw(raw);
        if (media !== null) {
          // НАХОДКА F1: пустой file_id проходит как валидное медиа (пример ниже).
          // Здесь фиксируем только тип, строгость - в примере-пине.
          assert.equal(typeof media.fileId, "string");
          assert.equal(typeof media.tag, "string");
          assert.equal(typeof media.transcribe, "boolean");
        }
        assert.deepEqual(mediaFromRaw(raw), media);
      },
    ),
    { seed: SEED, numRuns: 300 },
  );
});

test(`contentKeys: только ascii-имена, сортировка, уникальность (seed ${SEED})`, () => {
  fc.assert(
    fc.property(recordArb(1), (raw) => {
      const names = contentKeyNames([raw]);
      assert.deepEqual(names, [...new Set(names)].sort());
      for (const name of names) {
        assert.match(name, /^[a-z0-9_]+$/u);
        assert.ok(!["text", "caption", "message_id", "chat"].includes(name));
      }
    }),
    { seed: SEED, numRuns: 300 },
  );
});

// --- Примеры тупого пользователя: фиксированные входы ---

test("пример: файл вместо текста - document с именем 50к знаков читается", () => {
  const raw: TelegramRawMessage = {
    message_id: 7,
    chat: { id: 77, type: "private" },
    from: { id: 42, is_bot: false },
    document: { file_id: "f1", file_name: "отчёт".repeat(20_000) },
  };
  assert.deepEqual(messageParts(raw), [raw]);
  const media = mediaFromRaw(raw);
  assert.equal(media?.tag, "document");
  assert.equal(media?.fileId, "f1");
  assert.deepEqual(contentKeyNames([raw]), ["document"]);
});

test("пример: форвард с форварда - вложенный origin не роняет разбор", () => {
  const raw: TelegramRawMessage = {
    message_id: 8,
    text: "глянь",
    forward_origin: {
      type: "user",
      sender_user: { first_name: "Мама", last_name: "📅" },
    },
    reply_to_message: {
      message_id: 1,
      text: "исходное",
      forward_origin: {
        type: "channel",
        chat: { title: "Канал]] [[подделка" },
      },
    },
  };
  assert.deepEqual(messageParts(raw), [raw]);
  assert.equal(mediaFromRaw(raw), null);
});

test("пример: один апдейт дважды - чистый слой идемпотентен", () => {
  const raw: TelegramRawMessage = {
    message_id: 9,
    text: "напомни завтра",
    reply_to_message: { message_id: 2, text: "ок" },
  };
  assert.deepEqual(messageParts(raw), messageParts(raw));
  assert.deepEqual(contentKeyNames([raw]), contentKeyNames([raw]));
});

test("пример: эмодзи вместо всего - одни смайлы в тексте и подписи", () => {
  const raw: TelegramRawMessage = {
    message_id: 10,
    text: "📅⏰🎉".repeat(1000),
    caption: "📎".repeat(5000),
  };
  assert.deepEqual(messageParts(raw), [raw]);
  assert.equal(mediaFromRaw(raw), null);
});

test("photo с пустым file_id — не медиа, а нечитаемая запись (F1)", () => {
  // Пустой file_id прошёл бы typeof-проверку и породил дохлый getFile.
  assert.equal(mediaFromRaw({ photo: [{ file_id: "" }] }), null);
  assert.equal(mediaFromRaw({ document: { file_id: "" } }), null);
  // Контроль: настоящий идентификатор по-прежнему даёт форму медиа.
  assert.deepEqual(mediaFromRaw({ photo: [{ file_id: "file-1" }] }), {
    fileId: "file-1",
    tag: "photo",
    transcribe: false,
  });
  assert.deepEqual(
    mediaFromRaw({ document: { file_id: "file-2", file_name: "a.pdf" } }),
    {
      fileId: "file-2",
      tag: "document",
      transcribe: false,
      mimeType: undefined,
      fileName: "a.pdf",
    },
  );
});

test("пример: медиагруппа из мусора - iva_parts с битыми частями", () => {
  const raw: TelegramRawMessage = {
    message_id: 11,
    iva_parts: [
      { message_id: 11, text: "первая" },
      null,
      42,
      "строка",
      [],
      { message_id: 12, photo: [{ file_id: "p" }] },
      { message_id: 13, document: { file_id: 7 } },
    ],
  };
  const parts = messageParts(raw);
  assert.equal(parts.length, 3);
  const photo = mediaFromRaw(parts[1]);
  assert.equal(photo?.tag, "photo");
  assert.equal(mediaFromRaw(parts[2]), null);
});
