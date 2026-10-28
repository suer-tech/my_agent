/* eslint-disable @typescript-eslint/require-await -- async test doubles retain the transport contract. */
import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import fc from "fast-check";
import {
  TelegramDraftStream,
  draftText,
  telegramDraftFetch,
  DRAFT_TIMEOUT_MS,
} from "./telegram-draft.ts";

process.env.AGENT_LANGUAGE = "en";

type Call = { method: string; body?: Record<string, unknown> };
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));
function setup(
  t: TestContext,
  reply: (
    call: Call,
  ) => Promise<{ ok: boolean; body: unknown }> = async () => ({
    ok: true,
    body: { ok: true },
  }),
) {
  t.mock.timers.enable({
    apis: ["setTimeout", "setInterval", "Date"],
    now: 100_000,
  });
  const calls: Call[] = [];
  let own = true;
  const stream = new TelegramDraftStream(
    {
      chatId: "7",
      request(method, body) {
        const call = { method, body };
        calls.push(call);
        return reply(call);
      },
    },
    {
      identity: { sessionId: "s", turnId: "t", draftId: 41 },
      enabled: true,
      matches: () => own,
      getStatus: () => ({}),
    },
  );
  t.after(async () => {
    await stream.close(0);
    t.mock.timers.reset();
  });
  const advance = async (ms: number) => {
    t.mock.timers.tick(ms);
    await flush();
  };
  return {
    calls,
    stream,
    advance,
    reset: () => {
      own = false;
    },
    drafts: () => calls.filter((c) => c.method === "sendRichMessageDraft"),
    text: () =>
      String(
        (
          calls.filter((c) => c.method === "sendRichMessageDraft").at(-1)?.body
            ?.rich_message as { markdown?: string }
        )?.markdown,
      ),
  };
}

void test("draft starts with Thinking, can_stop and a numeric id; typing pulses throughout a quiet tool", async (t) => {
  const f = setup(t);
  await f.advance(0);
  assert.deepEqual(f.drafts()[0].body, {
    chat_id: "7",
    draft_id: 41,
    rich_message: { markdown: "<tg-thinking>Thinking…</tg-thinking>" },
    can_stop: true,
  });
  f.stream.requested(0, [
    { callId: "a", toolName: "bash", input: { command: "sleep 60" } },
  ]);
  await f.advance(1000);
  assert.match(f.text(), /⚙ <code>bash: sleep 60<\/code>/u);
  for (let i = 0; i < 4; i++) await f.advance(4500);
  await f.advance(2000);
  assert.equal(f.drafts().length, 3);
  assert.equal(f.calls.filter((c) => c.method === "sendChatAction").length, 5);
  await f.stream.close();
  const count = f.calls.length;
  await f.advance(40_000);
  assert.equal(f.calls.length, count);
});

void test("latest frame wins at one second; identical frames wait for refresh; tools leave on their callId", async (t) => {
  const f = setup(t);
  await f.advance(0);
  f.stream.append(0, "first ");
  f.stream.append(0, "second ");
  f.stream.append(0, "tail");
  await f.advance(999);
  assert.equal(f.drafts().length, 1);
  await f.advance(1);
  assert.equal(f.text(), "first second");
  f.stream.requested(0, [
    { callId: "a", toolName: "bash", input: { command: "one" } },
    { callId: "b", toolName: "read_file", input: { path: "two" } },
  ]);
  await f.advance(1000);
  assert.match(f.text(), /bash: one/u);
  assert.doesNotMatch(f.text(), /two/u);
  f.stream.result("a");
  await f.advance(1000);
  assert.match(f.text(), /read_file: two/u);
  f.stream.result("b");
  await f.advance(1000);
  assert.equal(f.text(), "first second");
  f.stream.append(1, "new step ");
  await f.advance(1000);
  assert.equal(f.text(), "new step");
  f.stream.append(0, "stale ");
  await f.advance(1000);
  assert.equal(f.text(), "new step");
});

void test("secret fragmented across deltas and secret tool arguments are scanned before trimming", async (t) => {
  const f = setup(t);
  await f.advance(0);
  const key = "sk-ant-api03-" + "T".repeat(60);
  f.stream.append(0, "hello ");
  for (const part of [key.slice(0, 10), key.slice(10, 20), key.slice(20)]) {
    f.stream.append(0, part);
    await f.advance(1000);
    assert.equal(f.text(), "hello");
  }
  f.stream.append(0, " done ");
  await f.advance(1000);
  assert.match(f.text(), /REDACTED/u);
  assert.doesNotMatch(f.text(), /sk-ant/u);
  f.stream.requested(0, [
    {
      callId: "a",
      toolName: "bash",
      input: { command: key + " <tg-button>`&" },
    },
  ]);
  await f.advance(1000);
  assert.doesNotMatch(f.text(), /sk-ant/u);
  assert.match(f.text(), /&lt;tg-button&gt;&#96;&amp;/u);
});

void test("partial silent marker and buttons stay hidden; tail reopens a code fence", () => {
  for (const text of [
    "<",
    "<!-- iva:sil",
    "<!-- iva:silent ",
    '<tg-button data="x" >unfinished ',
  ])
    assert.equal(draftText(text, false), "");
  assert.equal(
    draftText(
      '<!-- iva:silent -->\nhello <tg-button data="x">Yes</tg-button> ',
      false,
    ),
    "hello [Yes]",
  );
  assert.match(
    draftText("```ts\n" + "x".repeat(4000) + " ", false),
    /^```ts\n…/u,
  );
  assert.equal(draftText("tail", false), "");
  assert.equal(draftText("tail", true), "tail");
});

void test("quoted secrets in decoded tool arguments are redacted before the hint reaches Telegram", async (t) => {
  const f = setup(t);
  await f.advance(0);
  for (const command of [
    'export DB_PASSWORD="hunter2hunter2" && psql',
    'curl -d api_key="abcdefghijklmnopqrstuvwxyz0123" x',
  ]) {
    f.stream.requested(0, [
      { callId: "quoted", toolName: "bash", input: { command } },
    ]);
    await f.advance(1000);
    assert.doesNotMatch(f.text(), /hunter2hunter2|abcdefghijklmnopqrstuvwxyz/u);
    assert.match(f.text(), /REDACTED/u);
  }
});

void test("ordinary less-than signs and entities survive partial and completed frames", () => {
  for (const text of ["If x < 5 then retry ", "If x &lt; 5 then retry "]) {
    assert.equal(draftText(text, false), text.trim());
    assert.equal(draftText(text, true), text.trim());
  }
  for (const marker of [
    "<!-- iva:silent -->",
    "<tg-button",
    "<tg-button-row",
  ]) {
    for (let i = 1; i < marker.length; i++) {
      assert.equal(draftText(`hello ${marker.slice(0, i)}`, false), "hello");
      assert.equal(draftText(`hello ${marker.slice(0, i)}`, true), "hello");
    }
  }
});

void test("a tool hint keeps whole Unicode characters at its 60-unit boundary", async (t) => {
  const f = setup(t);
  await f.advance(0);
  f.stream.requested(0, [
    {
      callId: "unicode",
      toolName: "bash",
      input: { command: "a".repeat(53) + "😀 tail" },
    },
  ]);
  await f.advance(1000);
  assert.doesNotMatch(f.text(), /[\uD800-\uDFFF]/u);
  assert.match(f.text(), /bash: a+<\/code>$/u);
});

void test("an unchanged refresh rejected with 429 repeats at retry_after", async (t) => {
  const times: number[] = [];
  const f = setup(t, async (c) => {
    if (c.method !== "sendRichMessageDraft")
      return { ok: true, body: { ok: true } };
    times.push(Date.now() - 100_000);
    return times.length === 2
      ? { ok: false, body: { error_code: 429, parameters: { retry_after: 2 } } }
      : { ok: true, body: { ok: true } };
  });
  await f.advance(0);
  await f.advance(20_000);
  await f.advance(1_999);
  assert.deepEqual(times, [0, 20_000]);
  await f.advance(1);
  assert.deepEqual(times, [0, 20_000, 22_000]);
});

for (const code of [400, 403, 404])
  void test(`first draft ${code} disables drafts while typing survives`, async (t) => {
    const f = setup(t, async (c) =>
      c.method === "sendRichMessageDraft"
        ? { ok: false, body: { ok: false, error_code: code } }
        : { ok: true, body: { ok: true } },
    );
    await f.advance(0);
    f.stream.append(0, "hello ");
    await f.advance(30_000);
    assert.equal(f.drafts().length, 1);
    assert.ok(f.calls.filter((c) => c.method === "sendChatAction").length > 1);
  });

void test("three consecutive API errors after success disable drafts", async (t) => {
  let n = 0;
  const f = setup(t, async (c) => ({
    ok: c.method !== "sendRichMessageDraft" || ++n === 1,
    body: { error_code: 400 },
  }));
  await f.advance(0);
  f.stream.append(0, "new ");
  for (let i = 0; i < 4; i++) await f.advance(1000);
  assert.equal(f.drafts().length, 4);
});

void test("429 pauses until retry_after and sends the latest frame; close returns the pause", async (t) => {
  let n = 0;
  const f = setup(t, async (c) =>
    c.method === "sendRichMessageDraft" && ++n === 1
      ? { ok: false, body: { error_code: 429, parameters: { retry_after: 3 } } }
      : { ok: true, body: { ok: true } },
  );
  await f.advance(0);
  f.stream.append(0, "fresh ");
  await f.advance(2999);
  assert.equal(f.drafts().length, 1);
  await f.advance(1);
  assert.equal(f.text(), "fresh");
  await f.stream.close();
  await f.advance(40_000);
  assert.equal(f.drafts().length, 2);
});

void test("a network rejection skips a frame and retries by schedule", async (t) => {
  let n = 0;
  const f = setup(t, async (c) => {
    if (c.method === "sendRichMessageDraft" && ++n === 1)
      throw new Error("network");
    return { ok: true, body: { ok: true } };
  });
  await f.advance(0);
  await f.advance(1000);
  assert.equal(f.drafts().length, 2);
});

void test("reset self-check and failed typing close or continue within 4.5 seconds", async (t) => {
  const f = setup(t, async (c) => {
    if (c.method === "sendChatAction") throw new Error("typing failed");
    return { ok: true, body: { ok: true } };
  });
  await f.advance(0);
  await f.advance(4500);
  assert.equal(f.calls.filter((c) => c.method === "sendChatAction").length, 2);
  f.reset();
  await f.advance(4500);
  const count = f.calls.length;
  await f.advance(40_000);
  assert.equal(f.calls.length, count);
});

void test("in-flight close waits, and a late success arms no refresh", async (t) => {
  let resolve!: (v: { ok: boolean; body: unknown }) => void;
  const held = new Promise<{ ok: boolean; body: unknown }>((r) => {
    resolve = r;
  });
  const f = setup(t, (c) =>
    c.method === "sendRichMessageDraft"
      ? held
      : Promise.resolve({ ok: true, body: { ok: true } }),
  );
  await f.advance(0);
  let closed = false;
  const closing = f.stream.close().then(() => {
    closed = true;
  });
  await f.advance(1000);
  assert.equal(closed, false);
  resolve({ ok: true, body: { ok: true } });
  await closing;
  await f.advance(40_000);
  assert.equal(f.drafts().length, 1);
});

void test("cancel bounds an in-flight request to two seconds", async (t) => {
  const f = setup(t, (c) =>
    c.method === "sendRichMessageDraft"
      ? new Promise(() => {})
      : Promise.resolve({ ok: true, body: { ok: true } }),
  );
  await f.advance(0);
  let closed = false;
  const closing = f.stream.close(2000).then(() => {
    closed = true;
  });
  await f.advance(1999);
  assert.equal(closed, false);
  await f.advance(1);
  await closing;
  assert.equal(closed, true);
});

void test("api.fetch applies a deadline to draft and typing, preserves caller abort and leaves Outbox alone", async () => {
  const signals: (AbortSignal | null | undefined)[] = [];
  const fake: typeof fetch = async (_url, init) => {
    signals.push(init?.signal);
    return Response.json({ ok: true });
  };
  const fetcher = telegramDraftFetch(fake);
  const caller = new AbortController();
  await fetcher("https://example.test/botx/sendRichMessageDraft", {
    signal: caller.signal,
  });
  await fetcher("https://example.test/botx/sendChatAction");
  await fetcher("https://example.test/botx/sendRichMessage");
  assert.ok(signals[0] instanceof AbortSignal);
  assert.ok(signals[1] instanceof AbortSignal);
  assert.equal(signals[2], undefined);
  caller.abort();
  assert.equal(signals[0]?.aborted, true);
  assert.equal(DRAFT_TIMEOUT_MS, 10_000);
});

void test("PBT alternating steps, deltas, tools, final and reset preserves current frame (seed 101414)", async (t) => {
  t.mock.timers.enable({
    apis: ["setTimeout", "setInterval", "Date"],
    now: 100_000,
  });
  t.after(() => t.mock.timers.reset());
  await fc.assert(
    fc.asyncProperty(
      fc.array(
        fc.constantFrom("delta", "step", "tool", "result", "final", "reset"),
        { maxLength: 50 },
      ),
      async (ops) => {
        const calls: Call[] = [];
        let own = true;
        let closed = false;
        let step = 0;
        let text = "";
        let tool = false;
        const stream = new TelegramDraftStream(
          {
            chatId: "7",
            request: async (method, body) => {
              calls.push({ method, body });
              return { ok: true, body: { ok: true } };
            },
          },
          {
            enabled: true,
            identity: { sessionId: "s", turnId: "t", draftId: 41 },
            matches: () => own,
            getStatus: () => ({}),
          },
        );
        try {
          t.mock.timers.tick(0);
          await flush();
          for (const op of ops) {
            let before = calls.length;
            if (op === "delta") {
              stream.append(step, "word ");
              if (!closed) text += "word ";
            }
            if (op === "step") {
              step++;
              stream.append(step, "new ");
              if (!closed) text = "new ";
            }
            if (op === "tool") {
              stream.requested(step, [
                { callId: "a", toolName: "bash", input: { command: "go" } },
              ]);
              if (!closed) tool = true;
            }
            if (op === "result") {
              stream.result("a");
              tool = false;
            }
            if (op === "final") {
              await stream.close(0);
              closed = true;
            }
            if (op === "reset") {
              own = false;
              t.mock.timers.tick(4500);
              await flush();
              closed = true;
              before = calls.length;
            }
            t.mock.timers.tick(1000);
            await flush();
            if (closed) {
              assert.equal(calls.length, before);
              continue;
            }
            const frame = String(
              (
                calls.filter((c) => c.method === "sendRichMessageDraft").at(-1)
                  ?.body?.rich_message as { markdown: string }
              ).markdown,
            );
            const wanted: string =
              [text.trim(), tool ? "⚙ <code>bash: go</code>" : ""]
                .filter(Boolean)
                .join("\n\n") || "<tg-thinking>Thinking…</tg-thinking>";
            assert.equal(frame, wanted);
          }
        } finally {
          await stream.close(0);
        }
      },
    ),
    { seed: 101414, numRuns: 100 },
  );
});

void test("typing repeats every 4.5 seconds while its preceding request is still pending", async (t) => {
  const f = setup(t, (call) =>
    call.method === "sendChatAction"
      ? new Promise(() => {})
      : Promise.resolve({ ok: true, body: { ok: true } }),
  );
  await f.advance(0);
  await f.advance(4500);
  await f.advance(4500);
  assert.equal(
    f.calls.filter((call) => call.method === "sendChatAction").length,
    3,
  );
  await f.stream.close(0);
  const count = f.calls.length;
  await f.advance(40_000);
  assert.equal(f.calls.length, count);
});

void test("tool hint is one escaped line after CR, tabs and repeated spaces", async (t) => {
  const f = setup(t);
  await f.advance(0);
  f.stream.requested(0, [
    {
      callId: "a",
      toolName: "bash",
      input: { command: "\t<evil>  ` &\r something\nignored" },
    },
  ]);
  await f.advance(1000);
  assert.equal(
    f.text(),
    "⚙ <code>bash: &lt;evil&gt; &#96; &amp; something</code>",
  );
});

void test("quoted and backslash-containing installation secrets are redacted from full tool arguments", async (t) => {
  const previous = process.env.ASSISTANT_BEARER;
  const credential = 'installation"key\\value-' + "Q".repeat(40);
  process.env.ASSISTANT_BEARER = credential;
  t.after(() => {
    if (previous === undefined) delete process.env.ASSISTANT_BEARER;
    else process.env.ASSISTANT_BEARER = previous;
  });
  const f = setup(t);
  await f.advance(0);
  f.stream.requested(0, [
    {
      callId: "a",
      toolName: "bash",
      input: { command: `echo "${credential}"`, nested: { credential } },
    },
  ]);
  await f.advance(1000);
  assert.doesNotMatch(f.text(), /installation/u);
  assert.match(f.text(), /REDACTED/u);
});

void test("PBT tail preserves fence type and width (seed 101417)", () => {
  fc.assert(
    fc.property(
      fc.integer({ min: 3, max: 8 }),
      fc.constantFrom("`", "~"),
      (width, mark) => {
        const opener = mark.repeat(width) + "ts";
        const wrongType = (mark === "`" ? "~" : "`").repeat(width);
        for (const content of [wrongType, mark.repeat(width - 1)]) {
          assert.ok(
            draftText(
              `${opener}\n${content}\n${"x".repeat(4000)} `,
              false,
            ).startsWith(`${opener}\n…`),
          );
        }
        assert.ok(
          draftText(
            `${opener}\nx\n${mark.repeat(width)}\n${"x".repeat(4000)} `,
            false,
          ).startsWith("…"),
        );
      },
    ),
    { seed: 101417, numRuns: 100 },
  );
});
