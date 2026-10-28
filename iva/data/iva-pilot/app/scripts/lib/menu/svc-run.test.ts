/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registration promises. */
import test from "node:test";
import assert from "node:assert/strict";

import {
  stripAnsi,
  elapsed,
  tailText,
  currentRun,
  cancelRun,
  startProcess,
  startUnit,
  resetForTests,
} from "./svc-run.ts";
import type {
  ExecFileImplementation,
  RunOptions,
  ServiceRun,
} from "./svc-run.ts";

// edit-мок: копит markdown, который тикер отдаёт движку (своей дороги в Telegram у
// раннера нет — кнопка отмены живёт строкой в тексте прогресса).
function makeEdit() {
  const calls: string[] = [];
  const edit: RunOptions["edit"] = (markdown) => {
    calls.push(markdown);
    return Promise.resolve({ ok: true });
  };
  return { edit, calls };
}

const baseOpts = (
  edit: RunOptions["edit"],
  over: Partial<RunOptions> = {},
) => ({
  edit,
  chatId: 10,
  messageId: 7,
  attached: () => true,
  progressView: (run: ServiceRun) => ({ text: `работаю ${run.lastLine}` }),
  onFinish: () => {},
  tickMs: 15,
  timeoutMs: 5_000,
  pollMs: 5,
  ...over,
});

const waitFor = async (fn: () => unknown, ms = 3000): Promise<true> => {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (fn()) return true;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error("waitFor timeout");
};

test("stripAnsi: срезает цветовые и курсорные коды", () => {
  assert.equal(stripAnsi("\x1b[32m✓ ok\x1b[0m\r"), "✓ ok");
  assert.equal(stripAnsi("\x1b[?25lstep\x1b[?25h"), "step");
});

test("startProcess: успех — done, tail собран, прогресс шёл markdown-строками", async () => {
  resetForTests();
  const { edit, calls } = makeEdit();
  const finished: { value: ServiceRun | null } = { value: null };
  const run = startProcess(
    "doc",
    {
      argv: [
        process.execPath,
        "-e",
        "console.log('step one'); console.log('step two')",
      ],
    },
    baseOpts(edit, {
      onFinish: (r) => {
        finished.value = r;
      },
    }),
  );
  assert.ok(run);
  await waitFor(() => finished.value);
  assert.ok(finished.value);
  assert.equal(finished.value.status, "done");
  assert.deepEqual(finished.value.tail, ["step one", "step two"]);
  assert.equal(currentRun(), run);
  // хотя бы один прогресс-эдит, и он ушёл движку готовым markdown-текстом
  assert.ok(calls.length >= 1);
  assert.ok(calls.some((markdown) => markdown.startsWith("работаю ")));
});

test("startProcess: exit 1 — failed; второй старт при running — null", async () => {
  resetForTests();
  const { edit } = makeEdit();
  const finished: { value: ServiceRun | null } = { value: null };
  const run = startProcess(
    "doc",
    {
      argv: [process.execPath, "-e", "setTimeout(()=>process.exit(1), 150)"],
    },
    baseOpts(edit, {
      onFinish: (r) => {
        finished.value = r;
      },
    }),
  );
  assert.ok(run);
  assert.equal(
    startProcess(
      "cln",
      { argv: [process.execPath, "-e", "0"] },
      baseOpts(edit),
    ),
    null,
  );
  await waitFor(() => finished.value);
  assert.ok(finished.value);
  assert.equal(finished.value.status, "failed");
});

test("cancelRun: SIGTERM ребёнку, статус cancelled", async () => {
  resetForTests();
  const { edit } = makeEdit();
  const finished: { value: ServiceRun | null } = { value: null };
  startProcess(
    "cln",
    {
      argv: [process.execPath, "-e", "setTimeout(()=>{}, 60000)"],
    },
    baseOpts(edit, {
      onFinish: (r) => {
        finished.value = r;
      },
    }),
  );
  await waitFor(() => currentRun()?.status === "running");
  assert.equal(cancelRun(), true);
  await waitFor(() => finished.value);
  assert.ok(finished.value);
  assert.equal(finished.value.status, "cancelled");
  assert.equal(cancelRun(), false); // уже не running
});

test("startProcess: таймаут убивает и даёт status timeout", async () => {
  resetForTests();
  const { edit } = makeEdit();
  const finished: { value: ServiceRun | null } = { value: null };
  startProcess(
    "doc",
    {
      argv: [process.execPath, "-e", "setTimeout(()=>{}, 60000)"],
    },
    baseOpts(edit, {
      timeoutMs: 100,
      onFinish: (r) => {
        finished.value = r;
      },
    }),
  );
  await waitFor(() => finished.value);
  assert.ok(finished.value);
  assert.equal(finished.value.status, "timeout");
});

test("attached()=false: тикер молчит, процесс всё равно доезжает", async () => {
  resetForTests();
  const { edit, calls } = makeEdit();
  const finished: { value: ServiceRun | null } = { value: null };
  startProcess(
    "doc",
    {
      argv: [process.execPath, "-e", "console.log('quiet')"],
    },
    baseOpts(edit, {
      attached: () => false,
      onFinish: (r) => {
        finished.value = r;
      },
    }),
  );
  await waitFor(() => finished.value);
  assert.ok(finished.value);
  assert.equal(finished.value.status, "done");
  assert.equal(calls.length, 0);
});

test("a synchronous onFinish failure is contained after process completion", async () => {
  resetForTests();
  const { edit } = makeEdit();
  let finishCalled = false;
  const run = startProcess(
    "doc",
    { argv: [process.execPath, "-e", "process.exit(0)"] },
    baseOpts(edit, {
      onFinish: () => {
        finishCalled = true;
        throw new Error("injected synchronous finish failure");
      },
    }),
  );

  assert.ok(run);
  await waitFor(() => run.status !== "running");
  assert.equal(finishCalled, true);
  assert.equal(run.status, "done");
});

test("startUnit: oneshot activating→inactive = done, журнал в tail", async () => {
  resetForTests();
  const { edit } = makeEdit();
  const active = ["activating", "activating", "inactive"];
  const execFileImpl: ExecFileImplementation = (
    cmd,
    args,
    _options,
    callback,
  ) => {
    const a = args.join(" ");
    if (a.includes("start")) return callback(null, "", "");
    if (a.includes("is-active"))
      return callback(null, active.shift() ?? "inactive", "");
    if (cmd === "journalctl")
      return callback(null, "sync ok\ncleanup ok\n", "");
    return callback(null, "", "");
  };
  const finished: { value: ServiceRun | null } = { value: null };
  startUnit(
    "mem",
    { unit: "iva-brain.service" },
    baseOpts(edit, {
      execFileImpl,
      onFinish: (r) => {
        finished.value = r;
      },
    }),
  );
  await waitFor(() => finished.value);
  assert.ok(finished.value);
  assert.equal(finished.value.status, "done");
  assert.deepEqual(finished.value.tail, ["sync ok", "cleanup ok"]);
});

test("startUnit: failed юнит — status failed", async () => {
  resetForTests();
  const { edit } = makeEdit();
  const execFileImpl: ExecFileImplementation = (
    cmd,
    args,
    _options,
    callback,
  ) => {
    const a = args.join(" ");
    if (a.includes("start")) return callback(null, "", "");
    if (a.includes("is-active")) {
      const error = Object.assign(new Error("x"), { code: 3 });
      return callback(error, "failed", "");
    }
    if (cmd === "journalctl") return callback(null, "boom\n", "");
    return callback(null, "", "");
  };
  const finished: { value: ServiceRun | null } = { value: null };
  startUnit(
    "mem",
    { unit: "iva-brain.service" },
    baseOpts(edit, {
      execFileImpl,
      onFinish: (r) => {
        finished.value = r;
      },
    }),
  );
  await waitFor(() => finished.value);
  assert.ok(finished.value);
  assert.equal(finished.value.status, "failed");
  assert.deepEqual(finished.value.tail, ["boom"]);
});

test("elapsed/tailText: формат MM:SS и обрезка хвоста с конца", () => {
  const run = {
    startedAt: Date.now() - 65_000,
    finishedAt: Date.now(),
    tail: ["a".repeat(900), "b".repeat(900)],
  };
  assert.equal(elapsed(run), "01:05");
  const t = tailText(run, 1000);
  assert.ok(t.length <= 1000);
  assert.ok(t.includes("b"));
});
