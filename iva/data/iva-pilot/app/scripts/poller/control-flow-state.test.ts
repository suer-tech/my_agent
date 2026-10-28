/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns test registration. */
// Дешёвый CRAP-тест предиката визарда: мусор и частично валидные объекты — false,
// полное валидное состояние — true. Seed в имени; при провале подставь path.
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import { isTelegramFlowState } from "./control.ts";

const SEED = 20_260_913;

const valid = (): { flow: string } & Record<string, unknown> => ({
  flow: "model",
  chatId: 7,
  userId: "42",
  createdAt: 1_700_000_000_000,
  msgId: null,
  provider: "ollama",
  modelOptions: [],
  model: "x",
  efforts: [],
  effort: null,
  step: "intro",
  awaitText: null,
  screen: null,
  page: 0,
  data: {},
});

test("isTelegramFlowState: валидное состояние проходит (seed 20260913)", () => {
  assert.equal(isTelegramFlowState(valid()), true);
  assert.equal(
    isTelegramFlowState({ ...valid(), chatId: "7", userId: 42, msgId: 11 }),
    true,
    "id строкой/числом и живой msgId — тоже валидны",
  );
});

const REQUIRED_KEYS = [
  "flow",
  "chatId",
  "userId",
  "createdAt",
  "msgId",
  "page",
  "data",
] as const;

type RequiredKey = (typeof REQUIRED_KEYS)[number];

// Заведомо невалидные значения по ключам: ни одно не проходит свой пункт предиката
// (массивы для data валидны — объект не-null, — поэтому их здесь нет).
const WRONG: Record<RequiredKey, readonly unknown[]> = {
  flow: [42, null, true, {}],
  chatId: [null, true, {}, []],
  userId: [null, false, {}],
  createdAt: ["x", null, true, {}],
  msgId: ["x", true, {}],
  page: ["0", null, false],
  data: [null, 42, "x", true],
};

test("isTelegramFlowState: мусор и недозаполненное — false (seed 20260913)", () => {
  // На каждый обязательный ключ — вариант «ключ отсутствует» и «ключ не того
  // типа», остальное валидное: срез префиксов держал ноль на 7 из 8 пунктов.
  const perKey = REQUIRED_KEYS.map((victim) =>
    fc.oneof(
      fc.record({
        victim: fc.constant(victim),
        mode: fc.constant("missing" as const),
      }),
      ...WRONG[victim].map((bad) =>
        fc.record({
          victim: fc.constant(victim),
          mode: fc.constant("wrong" as const),
          bad: fc.constant(bad),
        }),
      ),
    ),
  );
  fc.assert(
    fc.property(
      fc.oneof(...perKey),
      ({
        victim,
        mode,
        bad,
      }: {
        victim: RequiredKey;
        mode: "missing" | "wrong";
        bad?: unknown;
      }) => {
        const obj: Record<string, unknown> = { ...valid() };
        if (mode === "missing") delete obj[victim];
        else obj[victim] = bad;
        assert.equal(
          isTelegramFlowState(obj as never),
          false,
          `${mode} ${victim} прошёл предикат: ${JSON.stringify(obj)?.slice(0, 120)}`,
        );
      },
    ),
    { seed: SEED, numRuns: 300 },
  );
});
