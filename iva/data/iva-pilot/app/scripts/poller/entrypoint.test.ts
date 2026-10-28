import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

void test("the bridge shim enters main through a current symlink instead of silently exiting", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-bridge-current-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  mkdirSync(join(dir, "versions"));
  symlinkSync(
    join(import.meta.dirname, "../.."),
    join(dir, "versions/v1"),
    "dir",
  );
  symlinkSync("versions/v1", join(dir, "current"), "dir");
  const result = spawnSync(
    process.execPath,
    [join(dir, "current/scripts/telegram-poll.mjs")],
    {
      encoding: "utf8",
      timeout: 10_000,
      env: {
        ...process.env,
        TELEGRAM_BOT_TOKEN: "",
        ASSISTANT_DATA_DIR: join(dir, "data"),
      },
    },
  );
  // Preflight stops before a process guard or any Bot API request. The old guard
  // would exit zero without calling main at all when argv names the symlink.
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /no TELEGRAM_BOT_TOKEN in .env/u);
});
