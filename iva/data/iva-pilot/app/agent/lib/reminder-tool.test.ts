import test from "node:test";
import assert from "node:assert/strict";
import { chatOfTurn } from "./reminder-tool.ts";

void test("chatOfTurn: Telegram-ход отдаёт чат и тему, ход без атрибутов - null", () => {
  assert.deepEqual(
    chatOfTurn({
      session: {
        auth: {
          current: {
            attributes: { chat_id: "-100777", message_thread_id: "835397" },
          },
        },
      },
    }),
    { id: "-100777", threadId: "835397" },
  );
  assert.deepEqual(
    chatOfTurn({
      session: { auth: { current: { attributes: { chat_id: "42" } } } },
    }),
    { id: "42", threadId: null },
  );
  assert.equal(chatOfTurn({ session: { auth: { current: null } } }), null);
  assert.equal(chatOfTurn({}), null);
  assert.equal(
    chatOfTurn({
      session: { auth: { current: { attributes: { chat_id: "" } } } },
    }),
    null,
  );
});
