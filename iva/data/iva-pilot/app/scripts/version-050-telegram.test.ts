import "./fixtures/no-host-anthropic.ts";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createTelegramUpdateReporter } from "./lib/telegram-status.ts";
import type { inspectUpstream } from "./lib/update-check.ts";

void test("Telegram update offer and completion preserve versions across the minor boundary on the wire", async (t) => {
  const data = mkdtempSync(join(tmpdir(), "iva-version-050-telegram-"));
  const before = { ...process.env };
  process.env.ASSISTANT_DATA_DIR = data;
  process.env.TELEGRAM_BOT_TOKEN = "fixture";
  process.env.TELEGRAM_ALLOWED_USER_IDS = "42";
  process.env.AGENT_LANGUAGE = "en";
  t.after(() => {
    for (const key of [
      "ASSISTANT_DATA_DIR",
      "TELEGRAM_BOT_TOKEN",
      "TELEGRAM_ALLOWED_USER_IDS",
      "AGENT_LANGUAGE",
    ]) {
      if (before[key] === undefined) delete process.env[key];
      else process.env[key] = before[key];
    }
    rmSync(data, { recursive: true, force: true });
  });
  const { handleUpdateCheck } = await import("./poller/update-flow.ts");
  const bodies: string[] = [];
  const fetchImpl: typeof fetch = (_url, init) => {
    assert.equal(typeof init?.body, "string");
    if (typeof init?.body === "string") bodies.push(init.body);
    return Promise.resolve(
      new Response(JSON.stringify({ ok: true, result: { message_id: 73 } })),
    );
  };
  t.mock.method(globalThis, "fetch", fetchImpl);
  for (const [from, to] of [
    ["0.4.17", "0.5.0"],
    ["0.4.9", "0.5.0"],
    ["0.5.0", "0.5.1"],
  ]) {
    const info: Awaited<ReturnType<typeof inspectUpstream>> = {
      beta: false,
      branch: "main",
      currentBranch: "main",
      legacyMigration: false,
      local: "old",
      remote: "new",
      behind: 1,
      localVersion: from,
      remoteVersion: to,
      hasCommitUpdate: true,
      hasVersionUpdate: true,
      updaterTooOld: false,
    };
    const saved: string[] = [];
    assert.equal(
      await handleUpdateCheck(42, {
        inspectImpl: () => Promise.resolve(info),
        markNotifiedImpl: (_root, version) => {
          saved.push(version);
          return Promise.resolve();
        },
        envImpl: () => Promise.resolve({ MODEL_PROVIDER: "ollama" }),
      }),
      true,
    );
    assert.ok(bodies.at(-1)?.includes(`v${from} → v${to}`));
    assert.deepEqual(saved, [to]);
    const reporter = createTelegramUpdateReporter({
      token: "fixture",
      env: { MODEL_PROVIDER: "ollama" },
      job: { chatId: "42", messageId: 73, locale: "en" },
      fetchImpl,
    });
    assert.ok(reporter);
    assert.equal(
      await reporter.complete({ beforeVersion: from, afterVersion: to }),
      true,
    );
    assert.ok(bodies.at(-1)?.includes(`${from} → ${to}`));
    reporter.dispose();
  }
});
