/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { test } from "node:test";

// Контракт собственного eve, бэкпорт vercel/eve#2841 (issue #2839).
// Точка удаления: бамп eve, где оба теста зелёные на неизменённом upstream.
const eveRoot = dirname(
  createRequire(import.meta.url).resolve("eve/package.json"),
);
const skillPaths = (await import(
  join(eveRoot, "dist/src/shared/skill-paths.js")
)) as {
  resolveSandboxSkillRoot(input: { sandbox: unknown }): Promise<string>;
  resolveSandboxModelPath(input: {
    path: string;
    sandbox: unknown;
  }): Promise<string>;
};

function sandboxWithHome(home: string) {
  return {
    run: () =>
      Promise.resolve({ exitCode: 0, stdout: `${home}\n`, stderr: "" }),
  };
}

test("skill root falls back to /workspace/skills when the sandbox HOME is the filesystem root", async () => {
  const sandbox = sandboxWithHome("/");
  assert.equal(
    await skillPaths.resolveSandboxSkillRoot({ sandbox }),
    "/workspace/skills",
  );
  assert.equal(
    await skillPaths.resolveSandboxModelPath({
      path: "$HOME/.agents/skills/receipts/SKILL.md",
      sandbox,
    }),
    "/workspace/skills/receipts/SKILL.md",
  );
  assert.equal(
    await skillPaths.resolveSandboxModelPath({
      path: "$HOME/notes/todo.md",
      sandbox,
    }),
    "/notes/todo.md",
  );
});

test("skill root keeps the HOME prefix when the sandbox HOME is usable", async () => {
  const sandbox = sandboxWithHome("/home/user");
  assert.equal(
    await skillPaths.resolveSandboxSkillRoot({ sandbox }),
    "/home/user/.agents/skills",
  );
});

test("the tool loop announces skills as a system message, not a user message", () => {
  const source = readFileSync(
    join(eveRoot, "dist/src/harness/tool-loop.js"),
    "utf8",
  );
  const site = /get\(PendingSkillAnnouncementKey\);[^;]*;/.exec(source);
  assert.ok(
    site,
    "tool-loop no longer reads PendingSkillAnnouncementKey: re-check our eve source changes",
  );
  assert.match(
    site[0],
    /addSystem\(\{role:`system`,content:[a-zA-Z_$][\w$]*\}\)/,
    `announcement is not a system message: ${site[0]}`,
  );
  assert.doesNotMatch(
    site[0],
    /\.add\(/,
    `announcement is added as a user message: ${site[0]}`,
  );
});
