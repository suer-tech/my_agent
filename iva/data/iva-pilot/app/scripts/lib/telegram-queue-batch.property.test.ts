// Свойства склейки очереди Bridge (спека L5 v2, §3.2 и §3.6): выравнивание частей,
// граница пачки, склейка без пересортировки и снятие ровно набора одной записью.
// Вход враждебный: вложенные и пустые iva_parts, не-объекты внутри, update_id со
// скачками и убыванием, пробелы перед «/», суррогатные пары, ключ «__proto__».
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: при провале fast-check печатает строку вида
// `Property failed after N tests { seed: -1234567, path: "12:3:0", endOnFailure: true }`.
// Подставь её в параметры fc.assert — fc.assert(prop, { seed: -1234567, path: "12:3:0" }) —
// и прогон пройдёт заново байт в байт, включая shrink.
import test from "node:test";
import assert from "node:assert/strict";
import fc from "fast-check";
import { TELEGRAM_BUTTON_TAP_FIELD } from "#lib/telegram-parts.ts";
import {
  COLLECT_MAX_CHARS,
  COLLECT_MAX_PARTS,
  joinUpdates,
  queueBatchLength,
  updateParts,
} from "./telegram-collect.ts";
import {
  createQueueItem,
  removeQueueItems,
  type TelegramQueueDocument,
  type TelegramQueueMessage,
  type TelegramQueueUpdate,
} from "./telegram-queue.ts";

const PARTS_SEED = 20_261_010;
const BATCH_SEED = 20_261_011;
const REMOVE_SEED = 20_261_012;

type Node = Record<string, unknown>;

function isNode(value: unknown): value is Node {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Оракул независим от кода: лист — узел, у которого нет ни одной части-объекта.
function leafIds(node: Node): unknown[] {
  const children = Array.isArray(node.iva_parts)
    ? node.iva_parts.filter(isNode)
    : [];
  return children.length
    ? children.flatMap((child) => leafIds(child))
    : [node.message_id];
}

const partsShape = fc.letrec<{ node: Node }>((tie) => ({
  node: fc.oneof(
    { depthSize: "small", withCrossShrink: true },
    fc.record({ text: fc.string({ maxLength: 8 }) }),
    fc.record({
      text: fc.string({ maxLength: 8 }),
      iva_parts: fc.constant([]),
    }),
    fc.record({ text: fc.string({ maxLength: 8 }), iva_parts: fc.string() }),
    fc.record({
      text: fc.string({ maxLength: 8 }),
      iva_parts: fc.array(
        fc.oneof(
          { arbitrary: tie("node"), weight: 4 },
          { arbitrary: fc.constantFrom(42, null, "part", true), weight: 1 },
        ),
        { maxLength: 4 },
      ),
    }),
  ),
}));

// Каждому узлу — свой message_id в порядке обхода: по нему видно порядок листьев.
function numbered(node: Node, next = { id: 1 }): Node {
  const copy: Node = { ...node, message_id: next.id++ };
  if (Array.isArray(node.iva_parts)) {
    copy.iva_parts = (node.iva_parts as unknown[]).map((child) =>
      isNode(child) ? numbered(child, next) : child,
    );
  }
  return copy;
}

void test("property: parts come out flat, non-empty and in depth-first order", () => {
  fc.assert(
    fc.property(partsShape.node, (shape) => {
      const message = numbered(shape) as TelegramQueueMessage;
      const update: TelegramQueueUpdate = { update_id: 1, message };
      const parts = updateParts(update);

      assert.ok(parts.length > 0, "a message always has at least one part");
      for (const part of parts) {
        assert.equal(Object.hasOwn(part, "iva_parts"), false);
      }
      assert.deepEqual(
        parts.map((part) => part.message_id),
        leafIds(message),
      );
      if (!Object.hasOwn(message, "iva_parts")) {
        assert.equal(parts.length, 1);
        assert.equal(parts[0], message, "a plain message is its own part");
      }
    }),
    { seed: PARTS_SEED, numRuns: 400 },
  );
});

// --- Очередь чата: автор, вид, текст ---

type Kind =
  | "text"
  | "document"
  | "command"
  | "captionCommand"
  | "reply"
  | "tap"
  | "synthetic"
  | "merge"
  | "damagedMerge"
  | "contact";

const ALONE_KINDS: ReadonlySet<Kind> = new Set([
  "command",
  "captionCommand",
  "reply",
  "tap",
  "synthetic",
]);

type Spec = {
  author: "A" | "B" | "none";
  kind: Kind;
  text: string;
  extraParts: string[];
};

// Обычный текст начинается с буквы: иначе случайная строка «/…» стала бы командой, и
// оракулу пришлось бы дублировать правило кода.
const body = fc
  .string({
    unit: fc.constantFrom("a", "я", " ", "/", "😀", "\n"),
    maxLength: 5_000,
  })
  .map((text) => `x${text}`);

const spec: fc.Arbitrary<Spec> = fc.record({
  author: fc.constantFrom(
    "A" as const,
    "A" as const,
    "B" as const,
    "none" as const,
  ),
  kind: fc.constantFrom<Kind>(
    "text",
    "text",
    "text",
    "document",
    "command",
    "captionCommand",
    "reply",
    "tap",
    "synthetic",
    "merge",
    "damagedMerge",
    "contact",
  ),
  text: body,
  extraParts: fc.array(body, { maxLength: 4 }),
});

const AUTHOR_IDS = { A: 42, B: "7" } as const;

function buildUpdate(item: Spec, updateId: number, index: number) {
  const messageId = 1_000 + index * 10;
  const base: TelegramQueueMessage = {
    message_id: messageId,
    date: 1,
    chat: { id: -100, type: "supergroup" },
    ...(item.author === "none"
      ? {}
      : { from: { id: AUTHOR_IDS[item.author], is_bot: false } }),
  };
  const part = (text: string, offset: number): TelegramQueueMessage => ({
    ...base,
    message_id: messageId + offset,
    text,
  });
  let message: TelegramQueueMessage;
  switch (item.kind) {
    case "document":
      message = { ...base, caption: item.text, document: { file_id: "f" } };
      break;
    case "command":
      message = { ...base, text: `  /${item.text}` };
      break;
    case "captionCommand":
      message = {
        ...base,
        caption: ` /${item.text}`,
        document: { file_id: "f" },
      };
      break;
    case "reply":
      message = {
        ...base,
        text: item.text,
        reply_to_message: { message_id: 1, from: { id: 9, is_bot: true } },
      };
      break;
    case "tap":
      message = { ...base, text: item.text, [TELEGRAM_BUTTON_TAP_FIELD]: true };
      break;
    case "merge":
      message = {
        ...base,
        text: item.text,
        iva_parts: [item.text, ...item.extraParts].map(part),
      };
      break;
    case "damagedMerge":
      message = {
        ...base,
        text: item.text,
        iva_parts: [
          { ...part(item.text, 0), iva_parts: [] },
          {
            ...part("x-nested-carrier", 1),
            iva_parts: item.extraParts.map((text, offset) =>
              part(text, offset + 2),
            ),
          },
        ],
      };
      break;
    case "contact":
      message = { ...base, contact: { first_name: "Ada", phone_number: "1" } };
      break;
    default:
      message = { ...base, text: item.text };
  }
  return {
    update_id: item.kind === "synthetic" ? -Math.abs(updateId) - 1 : updateId,
    message,
  } satisfies TelegramQueueUpdate;
}

// Оракул частей: листья без собственных частей, как их прочтёт Inbound pipeline.
function oracleParts(update: TelegramQueueUpdate): Node[] {
  const walk = (node: Node): Node[] => {
    const children = Array.isArray(node.iva_parts)
      ? node.iva_parts.filter(isNode)
      : [];
    return children.length ? children.flatMap(walk) : [node];
  };
  return update.message ? walk(update.message) : [];
}

const chars = (node: Node) =>
  (typeof node.text === "string" ? node.text.length : 0) +
  (typeof node.caption === "string" ? node.caption.length : 0);

const queue = fc.array(spec, { minLength: 1, maxLength: 30 }).chain((items) =>
  fc
    .uniqueArray(fc.integer({ min: 1, max: 2_000_000_000 }), {
      minLength: items.length,
      maxLength: items.length,
    })
    .map((ids) => ({ items, ids })),
);

void test("property: a batch is a maximal run of one author within the part and character limits", () => {
  fc.assert(
    fc.property(queue, ({ items, ids }) => {
      const updates = items.map((item, index) =>
        buildUpdate(item, ids[index], index),
      );
      const alone = (index: number) =>
        ALONE_KINDS.has(items[index].kind) || items[index].author === "none";
      const n = queueBatchLength(updates);

      assert.ok(n >= 1 && n <= updates.length, `n=${n}`);
      if (Array.from({ length: n }, (_, index) => alone(index)).some(Boolean))
        assert.equal(n, 1, "an alone message never shares a batch");
      const head = items[0];
      const batchParts = updates.slice(0, n).flatMap(oracleParts);
      const batchChars = batchParts.reduce((sum, part) => sum + chars(part), 0);
      if (n > 1) {
        assert.ok(
          items.slice(0, n).every((item) => item.author === head.author),
        );
        assert.ok(batchParts.length <= COLLECT_MAX_PARTS);
        assert.ok(batchChars <= COLLECT_MAX_CHARS);
      }
      if (n < updates.length && !alone(0)) {
        const nextParts = oracleParts(updates[n]);
        assert.ok(
          alone(n) ||
            items[n].author !== head.author ||
            batchParts.length + nextParts.length > COLLECT_MAX_PARTS ||
            batchChars + nextParts.reduce((sum, part) => sum + chars(part), 0) >
              COLLECT_MAX_CHARS,
          `batch stopped early at ${n}`,
        );
      }

      const batch = updates.slice(0, n);
      const joined = joinUpdates(batch);
      if (n === 1) {
        assert.equal(joined, batch[0], "a batch of one leaves untouched");
        return;
      }
      assert.equal(joined.update_id, batch.at(-1)!.update_id);
      assert.equal(joined.message!.message_id, batch[0].message.message_id);
      const parts = joined.message!.iva_parts!;
      assert.deepEqual(parts, batch.flatMap(updateParts));
      assert.deepEqual(
        parts.map((part) => part.message_id),
        batchParts.map((part) => part.message_id),
      );
      for (const part of parts) {
        assert.equal(Object.hasOwn(part, "iva_parts"), false);
      }
    }),
    { seed: BATCH_SEED, numRuns: 300 },
  );
});

// --- Снятие набора ---

const KEYS = ["1:", "-100:7", "__proto__"] as const;

function documentOf(queues: [string, number[]][]): TelegramQueueDocument {
  return {
    version: 1,
    queues: Object.fromEntries(
      queues.map(([key, ids]) => [
        key,
        ids.map((id) =>
          createQueueItem(
            {
              update_id: id,
              message: { message_id: id, text: `m${id}` },
            },
            1,
          ),
        ),
      ]),
    ),
  };
}

const removal = fc
  .uniqueArray(fc.integer({ min: -50, max: 500 }), {
    minLength: 1,
    maxLength: 24,
  })
  .chain((ids) =>
    fc.record({
      layout: fc
        .array(fc.integer({ min: 0, max: KEYS.length - 1 }), {
          minLength: ids.length,
          maxLength: ids.length,
        })
        .map((slots) =>
          KEYS.map(
            (key, slot) =>
              [key, ids.filter((_, index) => slots[index] === slot)] as [
                string,
                number[],
              ],
          ).filter(([, items]) => items.length > 0),
        ),
      target: fc.constantFrom(...KEYS),
      pick: fc.oneof(
        fc.record({
          mode: fc.constant("prefix" as const),
          size: fc.nat({ max: 8 }),
        }),
        fc.record({
          mode: fc.constant("subset" as const),
          chosen: fc.subarray(ids),
        }),
        fc.record({
          mode: fc.constant("foreign" as const),
          chosen: fc.uniqueArray(fc.integer({ min: 600, max: 700 }), {
            minLength: 1,
            maxLength: 3,
          }),
        }),
      ),
    }),
  );

void test("property: removal takes exactly the batch from the head or nothing at all", () => {
  fc.assert(
    fc.property(removal, ({ layout, target, pick }) => {
      const document = documentOf(layout);
      // JSON сохраняет «__proto__» собственным ключом, как его хранит очередь.
      const before = JSON.parse(
        JSON.stringify(document),
      ) as TelegramQueueDocument;
      const current = Object.hasOwn(document.queues, target)
        ? document.queues[target].map((item) => item.updateId)
        : [];
      const chosen =
        pick.mode === "prefix"
          ? current.slice(0, Math.max(1, pick.size))
          : pick.chosen;
      if (chosen.length === 0) {
        assert.throws(
          () => removeQueueItems(document, target, chosen),
          /batch is empty/u,
        );
        return;
      }
      const present = current.filter((id) => chosen.includes(id));
      const atHead = present.every((id, index) => current[index] === id);

      if (present.length === 0) {
        const result = removeQueueItems(document, target, chosen);
        assert.equal(result.document, document);
        assert.equal(result.removed, 0);
        return;
      }
      if (!atHead) {
        assert.throws(
          () => removeQueueItems(document, target, chosen),
          /batch changed/u,
        );
        assert.deepEqual(document, before, "a refused removal changes nothing");
        return;
      }
      const result = removeQueueItems(document, target, chosen);
      assert.equal(result.removed, present.length);
      assert.deepEqual(document, before, "the input document stays intact");
      const rest = current.slice(present.length);
      if (rest.length === 0) {
        assert.equal(Object.hasOwn(result.document.queues, target), false);
      } else {
        assert.deepEqual(
          result.document.queues[target].map((item) => item.updateId),
          rest,
        );
      }
      for (const [key, ids] of layout) {
        if (key === target) continue;
        assert.ok(Object.hasOwn(result.document.queues, key));
        assert.deepEqual(
          result.document.queues[key].map((item) => item.updateId),
          ids,
        );
      }
      assert.equal(
        Object.getPrototypeOf(result.document.queues),
        Object.prototype,
      );
    }),
    { seed: REMOVE_SEED, numRuns: 400 },
  );
});
