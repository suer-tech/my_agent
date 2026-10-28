import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import fc from "fast-check";
import { telegramChannel, type TelegramHandle } from "eve/channels/telegram";
import {
  flushSettledTelegramQuestions,
  postTelegramQuestion,
  settleTelegramQuestions,
  type QuestionState,
} from "./telegram-question.ts";

type QuestionApiCall = { method: string; body: Record<string, unknown> };

async function questionApi(
  respond: (
    call: QuestionApiCall,
  ) => Promise<{ status: number; body: unknown }>,
) {
  const calls: QuestionApiCall[] = [];
  const server = createServer((request, response) => {
    void (async () => {
      const chunks: Uint8Array[] = [];
      for await (const chunk of request) {
        assert.ok(chunk instanceof Uint8Array);
        chunks.push(chunk);
      }
      const call = {
        method: request.url!.slice(1),
        body: JSON.parse(Buffer.concat(chunks).toString()) as Record<
          string,
          unknown
        >,
      };
      calls.push(call);
      const result = await respond(call);
      response.writeHead(result.status, { "content-type": "application/json" });
      response.end(JSON.stringify(result.body));
    })();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const tg: Pick<TelegramHandle, "chatId" | "request" | "post"> = {
    chatId: "7",
    post: () => {
      throw new Error("unexpected native question");
    },
    async request(method, body) {
      const response = await fetch(
        `http://127.0.0.1:${address.port}/${method}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      return {
        ok: response.ok,
        status: response.status,
        body: await response.json(),
      };
    },
  };
  return {
    tg,
    calls,
    close: () => {
      server.closeAllConnections();
      return new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}

void test("slow initial rich send keeps the delivered question reference on the Bot API wire", async (t) => {
  const api = await questionApi(async () => {
    await new Promise((resolve) => setTimeout(resolve, 5100));
    return { status: 200, body: { ok: true, result: { message_id: 9 } } };
  });
  t.after(api.close);
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  await postTelegramQuestion(
    {
      requestId: "slow",
      kind: "question",
      prompt: "Confirm?",
      allowFreeform: true,
      action: {
        callId: "c",
        input: {},
        kind: "tool-call",
        toolName: "ask_question",
      },
      options: [{ id: "yes", label: "Yes" }],
    },
    state,
    api.tg,
    { rich: true },
  );
  assert.equal(state.questionPreviews?.slow.messageId, 9);
  assert.equal(state.questionPreviews?.slow.rich, true);
  assert.equal(state.pendingFreeformReplies?.["9"], "slow");
  assert.deepEqual(
    api.calls.map(({ method }) => method),
    ["sendRichMessage"],
  );
  assert.equal(api.calls[0].body.chat_id, "7");
  assert.match(
    (api.calls[0].body.rich_message as { markdown: string }).markdown,
    /tg-button/u,
  );
});

async function assertRichSizeRecovery(prompt: string, label: string) {
  const api = await questionApi((call) => {
    const markdown = (call.body.rich_message as { markdown: string }).markdown;
    const tooLong = markdown.length > 32768;
    return Promise.resolve({
      status: tooLong ? 400 : 200,
      body: tooLong
        ? { ok: false, description: "Bad Request: message is too long" }
        : { ok: true, result: { message_id: 9 } },
    });
  });
  try {
    const preview = {
      messageId: 9,
      rich: true,
      prompt,
      labels: { yes: label },
    };
    const state: QuestionState = {
      chatId: "7",
      chatType: "private",
      conversationId: null,
      messageThreadId: null,
      questionPreviews: { q: preview },
    };
    const resolution = {
      requestId: "q",
      outcome: "answered" as const,
      response: { optionId: "yes" },
    };
    await settleTelegramQuestions([resolution], state, api.tg);
    assert.equal(Object.keys(state.questionPreviews ?? {}).length, 0);
    assert.equal(api.calls.length, 2);
    for (const call of api.calls) {
      assert.equal(call.method, "editMessageText");
      assert.equal(call.body.message_id, 9);
      assert.deepEqual(call.body.reply_markup, { inline_keyboard: [] });
      assert.doesNotMatch(JSON.stringify(call.body), /tg-button/u);
    }
    const first = (api.calls[0].body.rich_message as { markdown: string })
      .markdown;
    const last = (api.calls[1].body.rich_message as { markdown: string })
      .markdown;
    assert.ok(first.length > 32768);
    assert.ok(last.length <= 32768);
    assert.match(last, /Selected|Выбрано/u);
    assert.ok(last.includes(label.slice(0, 1)));
    for (const part of last.split("\n\n")) {
      assert.doesNotMatch(part, /(?<!\\)(?:\\\\)*\\$/u);
      assert.doesNotMatch(part, /[\uD800-\uDBFF]$/u);
    }
    assert.equal(preview.labels.yes, label);
    assert.equal(resolution.response.optionId, "yes");
    await flushSettledTelegramQuestions(state, api.tg);
    await flushSettledTelegramQuestions(
      JSON.parse(JSON.stringify(state)) as QuestionState,
      api.tg,
    );
    assert.equal(api.calls.length, 2);
  } finally {
    await api.close();
  }
}

void test("oversized rich status settles the same Bot API message once", async () => {
  await assertRichSizeRecovery("x".repeat(32760), "Yes");
});

void test("a transient shortened rich edit keeps the accepted choice for JSON recovery", async (t) => {
  t.mock.method(console, "error", () => {});
  let unavailable = true;
  const api = await questionApi((call) => {
    const markdown = (call.body.rich_message as { markdown: string }).markdown;
    return Promise.resolve(
      markdown.length > 32768
        ? {
            status: 400,
            body: {
              ok: false,
              description: "Bad Request: message is too long",
            },
          }
        : {
            status: unavailable ? 503 : 200,
            body: { ok: !unavailable, result: { message_id: 9 } },
          },
    );
  });
  t.after(api.close);
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
    questionPreviews: {
      q: {
        messageId: 9,
        rich: true,
        prompt: "x".repeat(32760),
        labels: { yes: "Yes" },
      },
    },
  };
  await settleTelegramQuestions(
    [{ requestId: "q", outcome: "answered", response: { optionId: "yes" } }],
    state,
    api.tg,
  );
  assert.equal(api.calls.length, 2);
  assert.ok(state.questionPreviews?.q);
  assert.match(state.questionPreviews.q.settledStatus!, /Yes/u);
  const saved = JSON.parse(JSON.stringify(state)) as QuestionState;
  unavailable = false;
  await flushSettledTelegramQuestions(saved, api.tg);
  assert.equal(Object.keys(saved.questionPreviews ?? {}).length, 0);
  assert.equal(api.calls.length, 3);
  assert.deepEqual(api.calls[2], api.calls[1]);
  await flushSettledTelegramQuestions(saved, api.tg);
  assert.equal(api.calls.length, 3);
});

void test("rich size recovery preserves accepted choices and valid literal text; seed 2771014", async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.constantFrom("x", "я", "\\*", "\\\\", "😀"),
      fc.integer({ min: 32760, max: 32768 }),
      fc.constantFrom("Yes", "Ответ", "*", "\\", "😀"),
      fc.integer({ min: 1, max: 32768 }),
      async (token, length, label, count) => {
        const prompt =
          token.repeat(Math.floor(length / token.length)) +
          "x".repeat(length % token.length);
        await assertRichSizeRecovery(prompt, label.repeat(count));
      },
    ),
    { seed: 2771014, numRuns: 30 },
  );
});

void test("authoritative resolution removes buttons; edit failure never loses accepted answer", async () => {
  const calls: Array<{ method: string; body: unknown }> = [];
  let fail = false;
  const tg = {
    chatId: "7",
    post(body: unknown) {
      calls.push({ method: "sendMessage", body });
      return Promise.resolve({ id: "9", raw: { text: "Confirm?" } });
    },
    request(method: string, body: unknown) {
      calls.push({ method, body });
      return Promise.resolve({
        ok: !fail,
        status: 200,
        body: { ok: !fail, result: { message_id: 9 } },
      });
    },
  } as Pick<TelegramHandle, "chatId" | "request" | "post">;
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  await postTelegramQuestion(
    {
      requestId: "q",
      action: {
        callId: "c",
        input: {},
        kind: "tool-call",
        toolName: "ask_question",
      },
      kind: "question",
      prompt: "Confirm?",
      options: [{ id: "yes", label: "Yes" }],
    },
    state,
    tg,
  );
  assert.ok(state.questionPreviews?.q);
  fail = true;
  await settleTelegramQuestions(
    [{ requestId: "q", outcome: "answered", response: { optionId: "yes" } }],
    state,
    tg,
  );
  assert.ok(state.questionPreviews?.q);
  fail = false;
  const resumed = JSON.parse(JSON.stringify(state)) as QuestionState;
  await flushSettledTelegramQuestions(resumed, tg);
  assert.equal(resumed.questionPreviews?.q, undefined);
  await flushSettledTelegramQuestions(state, tg);
  assert.equal(state.questionPreviews?.q, undefined);
  const last = calls.at(-1)!.body as {
    text: string;
    reply_markup: { inline_keyboard: unknown[] };
  };
  assert.match(last.text, /Yes/u);
  assert.deepEqual(last.reply_markup.inline_keyboard, []);
  assert.equal(calls.filter((call) => call.method === "sendMessage").length, 1);
});

void test("Eve compiles the input.resolved public handler into its adapter", () => {
  const callback = () => {};
  const channel = telegramChannel({ events: { "input.resolved": callback } });
  // Introspection in the contract test, not a runtime monkey patch.
  assert.equal(
    typeof Reflect.get(Reflect.get(channel, "adapter"), "input.resolved"),
    "function",
  );
});

void test("rich resolution edits embedded buttons and never echoes a freeform answer", async () => {
  const calls: Array<{ method: string; body: Record<string, unknown> }> = [];
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: 42,
  };
  const tg = {
    chatId: "7",
    messageThreadId: 42,
    post: () => {
      throw new Error("unexpected native fallback");
    },
    request(method: string, body: Record<string, unknown>) {
      calls.push({ method, body });
      return Promise.resolve({
        ok: true,
        status: 200,
        body: { ok: true, result: { message_id: 9 } },
      });
    },
  } as unknown as TelegramHandle;
  await postTelegramQuestion(
    {
      requestId: "q-rich",
      kind: "question",
      prompt: "Accept <literal>?",
      action: {
        callId: "c",
        input: {},
        kind: "tool-call",
        toolName: "ask_question",
      },
      options: [{ id: "yes", label: "Yes" }],
      allowFreeform: true,
    },
    state,
    tg,
    { rich: true },
  );
  assert.equal(calls[0].method, "sendRichMessage");
  assert.equal(calls[0].body.message_thread_id, 42);
  assert.match(
    (calls[0].body.rich_message as { markdown: string }).markdown,
    /tg-button/u,
  );
  assert.ok(Object.keys(state.hitlCallbacks ?? {}).length);
  assert.equal(state.pendingFreeformReplies?.["9"], "q-rich");
  await settleTelegramQuestions(
    [
      {
        requestId: "q-rich",
        outcome: "answered",
        response: { text: "private-freeform-value" },
      },
    ],
    state,
    tg,
  );
  assert.equal(calls[1].method, "editMessageText");
  assert.equal(calls[1].body.message_id, 9);
  assert.doesNotMatch(
    JSON.stringify(calls[1].body),
    /tg-button|private-freeform-value/u,
  );
  assert.deepEqual(calls[1].body.reply_markup, { inline_keyboard: [] });
  assert.deepEqual(state.hitlCallbacks, {});
  assert.deepEqual(state.pendingFreeformReplies, {});
  assert.deepEqual(state.questionPreviews, {});
});

void test("native ForceReply and Eve's literal long-question rendering are preserved", async () => {
  const calls: Record<string, unknown>[] = [];
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  const tg = {
    chatId: "7",
    post: (body: Record<string, unknown>) => {
      calls.push(body);
      return Promise.resolve({ id: "19", raw: {} });
    },
    request: () =>
      Promise.resolve({ ok: true, status: 200, body: { ok: true } }),
  } as unknown as TelegramHandle;
  const request = {
    requestId: "freeform",
    kind: "question" as const,
    prompt: "<literal> *not bold* " + "x".repeat(7000),
    action: {
      callId: "c",
      input: {},
      kind: "tool-call" as const,
      toolName: "ask_question",
    },
  };
  const { renderTelegramInputRequest } = await import("eve/channels/telegram");
  const expected = renderTelegramInputRequest(request, { ...state });
  await postTelegramQuestion(request, state, tg, { rich: true });
  assert.equal(calls[0].text, expected.text);
  assert.deepEqual(calls[0].reply_markup, expected.replyMarkup);
  assert.equal(state.pendingFreeformReplies?.["19"], "freeform");
  await settleTelegramQuestions(
    [
      {
        requestId: "freeform",
        outcome: "answered",
        response: { text: "secret-do-not-echo" },
      },
    ],
    state,
    tg,
  );
  assert.deepEqual(state.pendingFreeformReplies, {});
});

void test("rich send failure falls back once; failed rich edits never fall back to HTML or repost", async (t) => {
  t.mock.method(console, "error", () => {});
  const calls: string[] = [];
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  let fail = true;
  const tg = {
    chatId: "7",
    post: () => {
      calls.push("sendMessage");
      return Promise.resolve({ id: "29", raw: {} });
    },
    request: (method: string) => {
      calls.push(method);
      return Promise.resolve({
        ok: !fail,
        status: fail ? 400 : 200,
        body: { ok: !fail, result: { message_id: 29 } },
      });
    },
  } as unknown as TelegramHandle;
  const request = {
    requestId: "fallback",
    kind: "question" as const,
    prompt: "Confirm?",
    action: {
      callId: "c",
      input: {},
      kind: "tool-call" as const,
      toolName: "ask_question",
    },
    options: [{ id: "yes", label: "Yes" }],
  };
  await postTelegramQuestion(request, state, tg, { rich: true });
  assert.equal(state.questionPreviews?.fallback.rich, false);
  assert.deepEqual(calls, ["sendRichMessage", "sendMessage"]);
  fail = false;
  await postTelegramQuestion({ ...request, requestId: "rich" }, state, tg, {
    rich: true,
  });
  const before = calls.length;
  fail = true;
  await settleTelegramQuestions(
    [{ requestId: "rich", outcome: "answered", response: { optionId: "yes" } }],
    state,
    tg,
  );
  await flushSettledTelegramQuestions(
    JSON.parse(JSON.stringify(state)) as QuestionState,
    tg,
  );
  assert.deepEqual(calls.slice(before), ["editMessageText", "editMessageText"]);
  assert.ok(state.questionPreviews?.rich.settledStatus);
});

void test("Telegram unchanged result completes delivery; HTTP 200 with ok:false stays pending", async (t) => {
  t.mock.method(console, "error", () => {});
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  let unchanged = false;
  const tg = {
    chatId: "7",
    post: () => Promise.resolve({ id: "39", raw: {} }),
    request: () =>
      Promise.resolve({
        ok: true,
        status: 200,
        body: {
          ok: false,
          description: unchanged
            ? "Bad Request: message is not modified"
            : "Bad Request: cannot edit",
        },
      }),
  } as unknown as TelegramHandle;
  await postTelegramQuestion(
    {
      requestId: "q",
      kind: "question",
      prompt: "Confirm?",
      action: {
        callId: "c",
        input: {},
        kind: "tool-call",
        toolName: "ask_question",
      },
      options: [{ id: "no", label: "Cancel" }],
    },
    state,
    tg,
  );
  await settleTelegramQuestions(
    [{ requestId: "q", outcome: "denied", response: { optionId: "no" } }],
    state,
    tg,
  );
  assert.ok(state.questionPreviews?.q.settledStatus);
  unchanged = true;
  await flushSettledTelegramQuestions(state, tg);
  assert.deepEqual(state.questionPreviews, {});
});

void test("unresolved and unknown questions are never edited by recovery", async () => {
  let edits = 0;
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  const tg = {
    chatId: "7",
    post: () => Promise.resolve({ id: "9", raw: {} }),
    request: () => {
      edits++;
      return Promise.resolve({ ok: true, status: 200, body: { ok: true } });
    },
  } as unknown as TelegramHandle;
  await postTelegramQuestion(
    {
      requestId: "q",
      kind: "question",
      prompt: "Confirm?",
      action: {
        callId: "c",
        input: {},
        kind: "tool-call",
        toolName: "ask_question",
      },
      options: [{ id: "yes", label: "Yes" }],
    },
    state,
    tg,
  );
  await flushSettledTelegramQuestions(state, tg);
  await settleTelegramQuestions(
    [
      { requestId: "missing", outcome: "ignored" },
      { requestId: "__proto__", outcome: "invalid" },
    ],
    state,
    tg,
  );
  assert.equal(edits, 0);
  assert.ok(state.questionPreviews?.q);
});

void test("ambiguous rich posting failure never posts a duplicate native question", async () => {
  let posts = 0;
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  const tg = {
    chatId: "7",
    post: () => {
      posts++;
      return Promise.resolve({ id: "9", raw: {} });
    },
    request: () => Promise.reject(new Error("connection lost after send")),
  } as unknown as TelegramHandle;
  await assert.rejects(
    postTelegramQuestion(
      {
        requestId: "q",
        kind: "question",
        prompt: "Confirm?",
        action: {
          callId: "c",
          input: {},
          kind: "tool-call",
          toolName: "ask_question",
        },
        options: [{ id: "yes", label: "Yes" }],
      },
      state,
      tg,
      { rich: true },
    ),
  );
  assert.equal(posts, 0);
  assert.equal(state.questionPreviews, undefined);
});

void test("invalid or ignored resolution closes the question without claiming the rejected option was accepted", async () => {
  const edited: string[] = [];
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  const tg = {
    chatId: "7",
    post: () => Promise.resolve({ id: "9", raw: {} }),
    request: (_method: string, body: { text?: string }) => {
      edited.push(body.text ?? "");
      return Promise.resolve({ ok: true, status: 200, body: { ok: true } });
    },
  } as unknown as TelegramHandle;
  for (const outcome of ["invalid", "ignored"] as const) {
    await postTelegramQuestion(
      {
        requestId: outcome,
        kind: "question",
        prompt: "Confirm?",
        action: {
          callId: "c",
          input: {},
          kind: "tool-call",
          toolName: "ask_question",
        },
        options: [{ id: "yes", label: "Yes" }],
      },
      state,
      tg,
    );
    await settleTelegramQuestions(
      [{ requestId: outcome, outcome, response: { optionId: "yes" } }],
      state,
      tg,
    );
    assert.match(edited.at(-1)!, /Question closed|Вопрос закрыт/u);
    assert.doesNotMatch(edited.at(-1)!, /Selected|Выбрано/u);
  }
});

void test("deleted or permanently noneditable previews retire recovery metadata without another request", async () => {
  for (const rich of [false, true])
    for (const description of [
      "Bad Request: message to edit not found",
      "Bad Request: message can't be edited",
    ]) {
      const calls: string[] = [];
      const state: QuestionState = {
        chatId: "7",
        chatType: "private",
        conversationId: null,
        messageThreadId: null,
      };
      const tg = {
        chatId: "7",
        post: () => Promise.resolve({ id: "9", raw: {} }),
        request: (method: string) => {
          calls.push(method);
          return Promise.resolve({
            ok: method.startsWith("send"),
            status: method.startsWith("send") ? 200 : 400,
            body: method.startsWith("send")
              ? { ok: true, result: { message_id: 9 } }
              : { ok: false, description },
          });
        },
      } as unknown as TelegramHandle;
      await postTelegramQuestion(
        {
          requestId: "q",
          kind: "question",
          prompt: "Confirm?",
          action: {
            callId: "c",
            input: {},
            kind: "tool-call",
            toolName: "ask_question",
          },
          options: [{ id: "yes", label: "Yes" }],
        },
        state,
        tg,
        { rich },
      );
      const before = calls.length;
      await settleTelegramQuestions(
        [
          {
            requestId: "q",
            outcome: "answered",
            response: { optionId: "yes" },
          },
        ],
        state,
        tg,
      );
      await flushSettledTelegramQuestions(state, tg);
      await flushSettledTelegramQuestions(
        JSON.parse(JSON.stringify(state)) as QuestionState,
        tg,
      );
      assert.deepEqual(calls.slice(before), ["editMessageText"]);
      assert.deepEqual(state.questionPreviews, {});
      assert.deepEqual(state.hitlCallbacks, {});
    }
});

void test("long native question retires a rejected status edit after confirmed keyboard removal", async (t) => {
  t.mock.method(console, "error", () => {});
  for (const markupResult of ["removed", "deleted", "transient"] as const) {
    const calls: string[] = [];
    const state: QuestionState = {
      chatId: "7",
      chatType: "private",
      conversationId: null,
      messageThreadId: null,
    };
    const tg = {
      chatId: "7",
      post: () => Promise.resolve({ id: "9", raw: {} }),
      request: (method: string) => {
        calls.push(method);
        if (method === "editMessageText")
          return Promise.resolve({
            ok: false,
            status: 400,
            body: {
              ok: false,
              description: "Bad Request: message is too long",
            },
          });
        return Promise.resolve(
          markupResult === "removed"
            ? { ok: true, status: 200, body: { ok: true } }
            : {
                ok: false,
                status: markupResult === "deleted" ? 400 : 503,
                body: {
                  ok: false,
                  description:
                    markupResult === "deleted"
                      ? "Bad Request: message to edit not found"
                      : "Service unavailable",
                },
              },
        );
      },
    } as unknown as TelegramHandle;
    await postTelegramQuestion(
      {
        requestId: "q",
        kind: "question",
        prompt: "x".repeat(9000),
        action: {
          callId: "c",
          input: {},
          kind: "tool-call",
          toolName: "ask_question",
        },
        options: [{ id: "yes", label: "Yes" }],
      },
      state,
      tg,
    );
    await settleTelegramQuestions(
      [{ requestId: "q", outcome: "answered", response: { optionId: "yes" } }],
      state,
      tg,
    );
    await flushSettledTelegramQuestions(state, tg);
    if (markupResult === "transient") {
      assert.deepEqual(calls, [
        "editMessageText",
        "editMessageReplyMarkup",
        "editMessageText",
        "editMessageReplyMarkup",
      ]);
      assert.ok(state.questionPreviews?.q.settledStatus);
    } else {
      assert.deepEqual(calls, ["editMessageText", "editMessageReplyMarkup"]);
      assert.deepEqual(state.questionPreviews, {});
    }
    assert.deepEqual(state.hitlCallbacks, {});
  }
});

void test("transient preview backlog retries one edit pair per lifecycle and rotates failures", async (t) => {
  t.mock.method(console, "error", () => {});
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
  };
  const calls: Array<{ method: string; messageId: unknown }> = [];
  let id = 0;
  const tg = {
    chatId: "7",
    post: () => Promise.resolve({ id: String(++id), raw: {} }),
    request: (method: string, body: { message_id: unknown }) => {
      calls.push({ method, messageId: body.message_id });
      return Promise.resolve({ ok: false, status: 503, body: { ok: false } });
    },
  } as unknown as TelegramHandle;
  for (const requestId of ["first", "second", "third"])
    await postTelegramQuestion(
      {
        requestId,
        kind: "question",
        prompt: "Confirm?",
        action: {
          callId: "c",
          input: {},
          kind: "tool-call",
          toolName: "ask_question",
        },
        options: [{ id: "yes", label: "Yes" }],
      },
      state,
      tg,
    );
  await settleTelegramQuestions(
    ["first", "second", "third"].map((requestId) => ({
      requestId,
      outcome: "answered" as const,
      response: { optionId: "yes" },
    })),
    state,
    tg,
  );
  assert.deepEqual(calls, [
    { method: "editMessageText", messageId: "1" },
    { method: "editMessageReplyMarkup", messageId: "1" },
  ]);
  assert.deepEqual(Object.keys(state.questionPreviews!), [
    "second",
    "third",
    "first",
  ]);
  const recovered = JSON.parse(JSON.stringify(state)) as QuestionState;
  await flushSettledTelegramQuestions(recovered, tg);
  assert.deepEqual(calls.slice(2), [
    { method: "editMessageText", messageId: "2" },
    { method: "editMessageReplyMarkup", messageId: "2" },
  ]);
  assert.deepEqual(Object.keys(recovered.questionPreviews!), [
    "third",
    "first",
    "second",
  ]);
  assert.deepEqual(recovered.hitlCallbacks, {});
});

void test("one flush shares the five-second deadline across text and keyboard edits", async (t) => {
  t.mock.method(console, "error", () => {});
  let now = 0;
  t.mock.method(performance, "now", () => now);
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const methods: string[] = [];
  const state: QuestionState = {
    chatId: "7",
    chatType: "private",
    conversationId: null,
    messageThreadId: null,
    questionPreviews: {
      q: {
        messageId: "9",
        prompt: "Confirm?",
        labels: {},
        rich: false,
        settledStatus: "Answer received",
      },
    },
  };
  const tg = {
    chatId: "7",
    request: (method: string) => {
      methods.push(method);
      return new Promise<never>(() => {});
    },
  } as unknown as TelegramHandle;
  const flushing = flushSettledTelegramQuestions(state, tg);
  now = 5000;
  t.mock.timers.tick(5000);
  await flushing;
  assert.deepEqual(methods, ["editMessageText"]);
  assert.ok(state.questionPreviews?.q.settledStatus);
});
