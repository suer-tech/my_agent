import "./lib/ts-esm-hooks.ts";
import assert from "node:assert/strict";
import fs from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { PassThrough } from "node:stream";
import { execFile, execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { open } from "node:fs/promises";
import fc from "fast-check";
import { Worker } from "node:worker_threads";
import test from "node:test";
import type { ToolContext } from "eve/tools";
const { default: grep } = await import("../agent/tools/grep.ts");

await test("grep cancellation returns and terminates the worker while a writerless FIFO stays open", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-grep-fifo-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, "input.txt");
  const fifo = join(dir, "fifo");
  writeFileSync(path, "fixture");
  execFileSync("mkfifo", [fifo]);
  let stream: fs.ReadStream | undefined;
  let readStarted = () => {};
  const reading = new Promise<void>((resolve) => {
    readStarted = resolve;
  });
  const originalRead = fs.createReadStream;
  t.mock.method(
    fs,
    "createReadStream",
    (...args: Parameters<typeof fs.createReadStream>) => {
      if (args[0] !== path) return originalRead(...args);
      stream = originalRead(fifo, args[1]);
      readStarted();
      return stream;
    },
  );
  syncBuiltinESMExports();
  t.after(() => {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  });
  const originalTerminate: unknown = Reflect.get(Worker.prototype, "terminate");
  assert.ok(typeof originalTerminate === "function");
  let terminated = false;
  t.mock.method(Worker.prototype, "terminate", async function (this: Worker) {
    const code: unknown = await Reflect.apply(originalTerminate, this, []);
    terminated = true;
    return code;
  });
  const controller = new AbortController();
  let settled = false;
  const work = Promise.resolve(
    grep.execute({ path, pattern: "fixture" }, {
      abortSignal: controller.signal,
    } as ToolContext),
  ).then((answer) => {
    settled = true;
    return answer;
  });
  try {
    await reading;
    await new Promise((resolve) => setTimeout(resolve, 50));
    controller.abort();
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(
      settled,
      true,
      "reply must arrive while FIFO open still waits for a writer",
    );
    assert.equal(
      terminated,
      true,
      "the search worker has exited before the reply",
    );
    assert.equal(
      stream?.closed,
      false,
      "cancellation is independent of filesystem close",
    );
    assert.equal(((await work) as { error?: string }).error, "поиск отменён");
  } finally {
    // Only teardown opens a writer, after the observations, to release the OS open request.
    const unblock = fs.openSync(
      fifo,
      fs.constants.O_RDWR | fs.constants.O_NONBLOCK,
    );
    try {
      await work;
      if (stream && !stream.closed)
        await new Promise<void>((resolve) => stream!.once("close", resolve));
    } finally {
      fs.closeSync(unblock);
    }
  }
});

await test("grep abort closes an unfinished line and its worker before more input arrives", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-grep-held-line-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, "held.txt");
  writeFileSync(path, "fixture");
  const stream = new PassThrough();
  stream.setEncoding("utf8");
  let readStarted = () => {};
  const reading = new Promise<void>((resolve) => {
    readStarted = resolve;
  });
  const originalRead = fs.createReadStream;
  t.mock.method(
    fs,
    "createReadStream",
    (...args: Parameters<typeof fs.createReadStream>) => {
      if (args[0] !== path) return originalRead(...args);
      readStarted();
      return stream;
    },
  );
  syncBuiltinESMExports();
  t.after(() => {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  });
  const originalTerminate: unknown = Reflect.get(Worker.prototype, "terminate");
  assert.ok(typeof originalTerminate === "function");
  let terminated = false;
  t.mock.method(Worker.prototype, "terminate", async function (this: Worker) {
    const code: unknown = await Reflect.apply(originalTerminate, this, []);
    terminated = true;
    return code;
  });
  const controller = new AbortController();
  let settled = false;
  const work = Promise.resolve(
    grep.execute({ path, pattern: "abc" }, {
      abortSignal: controller.signal,
    } as ToolContext),
  ).then((answer) => {
    settled = true;
    return answer;
  });
  try {
    await reading;
    stream.write("x".repeat(65536));
    await new Promise<void>((resolve) => setImmediate(resolve));
    controller.abort();
    await new Promise((resolve) => setTimeout(resolve, 150));
    assert.equal(
      settled,
      true,
      "abort must finish while the line remains incomplete",
    );
    assert.equal(stream.destroyed, true);
    assert.equal(terminated, true);
    assert.equal(((await work) as { error?: string }).error, "поиск отменён");
  } finally {
    if (!stream.destroyed) stream.end("\n");
    await work;
  }
});
for (const mode of ["deadline", "abort"]) {
  await test(`grep hears ${mode} while a hostile expression runs`, async (t) => {
    const dir = mkdtempSync(join(tmpdir(), "iva-grep-deadline-"));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const path = join(dir, "hostile.txt");
    writeFileSync(path, "a".repeat(40) + "!");
    const { stdout } = await promisify(execFile)(
      process.execPath,
      ["scripts/fixtures/grep-deadline.ts", path, mode],
      { timeout: 4000 },
    );
    const output = JSON.parse(stdout) as {
      result: { error: string };
      ticks: number;
    };
    assert.ok(output.ticks > 0, "event loop blocked");
    assert.match(
      output.result.error,
      mode === "deadline" ? /выражение слишком тяжёлое, упрости/u : /отмен/u,
    );
  });
}
await test("grep returns model text for malformed patterns and flags", async () => {
  for (const input of [{ pattern: "[" }, { pattern: "hello", flags: "bad" }]) {
    const output = await grep.execute(
      { ...input, path: import.meta.filename },
      { abortSignal: new AbortController().signal } as ToolContext,
    );
    assert.ok(
      typeof output === "object" && output !== null && "error" in output,
    );
  }
});

await test("grep stream preserves one-based lines and 300-character text (seed 101315)", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-grep-format-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, "lines.txt");
  await fc.assert(
    fc.asyncProperty(
      fc.array(
        fc
          .string({ minLength: 294, maxLength: 450 })
          .filter((s) => !s.includes("\n")),
        { minLength: 1, maxLength: 20 },
      ),
      async (parts) => {
        const lines = ["no match", ...parts.map((s) => `needle ${s}`), ""];
        writeFileSync(path, lines.join("\n"));
        const answer = (await grep.execute(
          { path, pattern: "^needle", flags: "g" },
          { abortSignal: new AbortController().signal } as ToolContext,
        )) as {
          count: number;
          matches: { file: string; line: number; text: string }[];
        };
        assert.equal(answer.count, parts.length);
        assert.deepEqual(
          answer.matches,
          lines.slice(1, -1).map((text, index) => ({
            file: path,
            line: index + 2,
            text: text.length > 300 ? text.slice(0, 300) + "…" : text,
          })),
        );
      },
    ),
    {
      seed: 101315,
      numRuns: 30,
      examples: [[["x".repeat(292), "x".repeat(293), "x".repeat(294)]]],
    },
  );
});

await test("grep deadline excludes message transfer before expression execution", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-grep-transfer-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, "line.txt");
  writeFileSync(path, "needle\n");
  const original: unknown = Reflect.get(Worker.prototype, "postMessage");
  assert.ok(typeof original === "function");
  const pending = new Set<ReturnType<typeof setTimeout>>();
  t.after(() => {
    for (const timer of pending) clearTimeout(timer);
  });
  // Only the transport is delayed. The unmodified worker evaluates the actual expression.
  t.mock.method(
    Worker.prototype,
    "postMessage",
    function (this: Worker, value: unknown) {
      const timer = setTimeout(() => {
        pending.delete(timer);
        Reflect.apply(original, this, [value]);
      }, 1100);
      pending.add(timer);
    },
  );
  const answer = (await grep.execute({ path, pattern: "needle" }, {
    abortSignal: new AbortController().signal,
  } as ToolContext)) as { error?: string; count: number };
  assert.equal(answer.error, undefined);
  assert.equal(answer.count, 1);
});

await test("grep reads all 400 MiB for a final-chunk match and for an absent match", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-grep-large-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, "large.txt");
  const marker = "ПРИВЕТ МИР\n";
  const line = "x".repeat(512 - Buffer.byteLength(marker)) + marker;
  const block = Buffer.from(line.repeat(2048));
  const handle = await open(path, "w");
  try {
    for (let i = 0; i < 400; i++) await handle.write(block);
    await handle.write("needle final\n");
  } finally {
    await handle.close();
  }
  const answer = (await grep.execute({ path, pattern: "^needle final$" }, {
    abortSignal: new AbortController().signal,
  } as ToolContext)) as {
    error?: string;
    count: number;
    truncated: boolean;
    matches: { file: string; line: number; text: string }[];
  };
  assert.equal(answer.error, undefined);
  assert.equal(answer.count, 1);
  assert.equal(answer.truncated, false);
  assert.deepEqual(answer.matches, [
    {
      file: path,
      line: 400 * 2048 + 1,
      text: "needle final",
    },
  ]);
  const absent = await grep.execute(
    { path, pattern: "never-present-in-this-file" },
    {
      abortSignal: new AbortController().signal,
    } as ToolContext,
  );
  assert.deepEqual(absent, { count: 0, truncated: false, matches: [] });
  const limited = await grep.execute(
    { path, pattern: "ПРИВЕТ МИР$", flags: "iu" },
    {
      abortSignal: new AbortController().signal,
    } as ToolContext,
  );
  assert.deepEqual(limited, {
    count: 1000,
    truncated: true,
    matches: Array.from({ length: 1000 }, (_, i) => ({
      file: path,
      line: i + 1,
      text: "x".repeat(300) + "…",
    })),
  });
});

await test("grep hears cancellation between stream batches and terminates its worker", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-grep-batch-abort-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, "lines.txt");
  writeFileSync(path, "ordinary line\n".repeat(20000) + "needle final\n");
  const controller = new AbortController();
  const original: unknown = Reflect.get(Worker.prototype, "postMessage");
  assert.ok(typeof original === "function");
  let batches = 0;
  const observedWorkers = new Set<Worker>();
  t.mock.method(
    Worker.prototype,
    "postMessage",
    function (this: Worker, value: unknown) {
      observedWorkers.add(this);
      if (++batches === 1)
        this.on("message", (message: { kind: string }) => {
          if (message.kind === "result") controller.abort();
        });
      Reflect.apply(original, this, [value]);
    },
  );
  const answer = await grep.execute({ path, pattern: "needle" }, {
    abortSignal: controller.signal,
  } as ToolContext);
  assert.equal(batches, 1);
  assert.ok(typeof answer === "object" && answer !== null && "error" in answer);
  assert.match(String(answer.error), /поиск отменён/u);
  assert.equal(observedWorkers.size, 1);
  for (const worker of observedWorkers) assert.equal(worker.threadId, -1);
});
