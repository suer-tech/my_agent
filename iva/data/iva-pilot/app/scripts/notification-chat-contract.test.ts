import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";

import { notificationChat as authoredChat } from "../agent/lib/notification-chat.ts";
import { notificationChat as operationalChat } from "./lib/notification-chat.ts";

// Обе половины установки обязаны выбрать один и тот же чат владельца: authored-дерево не
// импортирует scripts/ (authored-tree-guard), поэтому функция живёт двумя копиями, и только
// этот тест держит их вместе.

const SEED = 20260912;

const CASES: ReadonlyArray<readonly [Record<string, string>, string]> = [
  [{ TELEGRAM_DIGEST_CHAT_ID: "555", TELEGRAM_ALLOWED_USER_IDS: "123" }, "555"],
  [
    { TELEGRAM_DIGEST_CHAT_ID: "", TELEGRAM_ALLOWED_USER_IDS: "123, 456" },
    "123",
  ],
  [{ TELEGRAM_ALLOWED_USER_IDS: " 123  456 " }, "123"],
  [{}, ""],
  [{ TELEGRAM_DIGEST_CHAT_ID: "   ", TELEGRAM_ALLOWED_USER_IDS: "456" }, "456"],
];

void test("both notificationChat copies resolve the same owner chat", () => {
  for (const [env, expected] of CASES) {
    assert.equal(authoredChat(env), expected);
    assert.equal(operationalChat(env), expected);
  }
});

void test(`both notificationChat copies agree on arbitrary env pairs (seed ${SEED})`, () => {
  fc.assert(
    fc.property(
      fc.record({
        TELEGRAM_DIGEST_CHAT_ID: fc.option(fc.string(), { nil: undefined }),
        TELEGRAM_ALLOWED_USER_IDS: fc.option(fc.string(), { nil: undefined }),
      }),
      (env) => {
        assert.equal(authoredChat(env), operationalChat(env));
      },
    ),
    { seed: SEED, numRuns: 300 },
  );
});
