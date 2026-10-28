/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Проводка гварда T2 в bash-тул: без этого теста мутация «не вызывать
// schedulerBypassViolation» оставляла весь скоп зелёным - гвард проверялся только сам по
// себе, а не то, что тул его спрашивает ДО exec.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import bash from "../agent/tools/bash.ts";

type BashInput = { command: string; timeoutMs?: number };
type BashResult = {
  stdout: string;
  stderr: string;
  exitCode: number;
  timedOut?: boolean;
};

async function executeBash(input: BashInput): Promise<BashResult> {
  return await (
    bash.execute as unknown as (input: BashInput) => Promise<BashResult>
  )(input);
}

const quote = (value: string) => `'${value.replaceAll("'", `'\\''`)}'`;

test("bash tool refuses an own timer before it is executed", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-bash-bypass-guard-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  // Маркер внутри подстановки: если тул всё-таки дойдёт до exec, `touch` создаст файл и
  // тест это увидит. Сам вызов из спеки блокируется первой же командной позицией.
  const marker = join(dir, "executed");
  const result = await executeBash({
    command: `systemd-run --user --on-active=60 iva notify x $(touch ${quote(marker)})`,
    timeoutMs: 1_000,
  });

  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /^ЗАБЛОКИРОВАНО:/);
  assert.match(result.stderr, /remind:/);
  assert.equal(result.stdout, "");
  assert.equal(result.timedOut, undefined);
  assert.equal(existsSync(marker), false, "команда не должна была исполниться");
});

test("bash tool executes a quoted grep pipeline with at and batch in data", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "iva-bash-plain-data-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const input = join(dir, "input.txt");
  writeFileSync(input, "zero\nx|batch\nzzz\n");
  const result = await executeBash({
    command: `grep -nE "a|batch" ${quote(input)} | head -20 | cut -d: -f2-`,
    timeoutMs: 1_000,
  });

  assert.equal(result.exitCode, 0, result.stderr);
  assert.equal(result.stdout, "x|batch\n");
  assert.equal(result.stderr, "");
});

test("bash tool still refuses iva restart before execution", async () => {
  const result = await executeBash({
    command: "iva restart",
    timeoutMs: 1_000,
  });

  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /^ЗАБЛОКИРОВАНО:/);
  assert.match(result.stderr, /\/restart/);
  assert.equal(result.stdout, "");
});
