/* eslint-disable @typescript-eslint/no-floating-promises -- node:test owns registrations. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  cpSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import fc from "fast-check";

const specs = new URL("../specs/", import.meta.url);
const scripts = readdirSync(specs).filter((name) => name.endsWith("-check.sh"));
type Rule = { cfg: string; expected: string };

// Exercise the actual shell entry points. Java and model exploration are deliberately absent.
function probe(script: string, mode: string, mutate?: (dir: string) => void) {
  const root = mkdtempSync(join(tmpdir(), "iva-tlc-probe-"));
  try {
    const dir = join(root, "specs");
    cpSync(specs, dir, { recursive: true });
    const source = readFileSync(join(dir, script), "utf8");
    const rules: Rule[] = [
      ...source.matchAll(/\bcheck\s+([A-Z][\w-]*)\s+(\w+)/g),
    ].map((match) => ({ cfg: match[1], expected: match[2] }));
    assert.ok(rules.length, script);
    mutate?.(dir);
    const fake = join(root, "fake-tlc");
    writeFileSync(
      fake,
      `#!${process.execPath}
const fs = require('node:fs');
const args = process.argv.slice(2);
const cfg = args[args.indexOf('-config') + 1];
const rules = JSON.parse(process.env.PROBE_RULES);
const index = fs.existsSync(process.env.PROBE_INDEX) ? Number(fs.readFileSync(process.env.PROBE_INDEX, 'utf8')) : 0;
fs.writeFileSync(process.env.PROBE_INDEX, String(index + 1));
const rule = rules[index];
if (!rule || !require('node:path').basename(cfg).startsWith(rule.cfg)) process.exit(97);
const text = fs.readFileSync(cfg, 'utf8');
const temporal = new RegExp('^PROPERT(?:Y|IES)\\\\s+' + rule.expected + '(?:\\\\s|$)', 'm').test(text);
const mode = process.env.PROBE_MODE;
let code = rule.expected === 'none' ? 0 : temporal ? 13 : 12;
if (rule.expected === 'none') {
  console.log('Model checking completed. No error has been found.');
  if (mode === 'bad-success-exit') code = 97;
  if (mode === 'error-before-success') console.log('Error: Unexpected runtime failure');
} else {
  if (mode === 'wrong-first-property') console.log('Error: Invariant UnexpectedInvariant is violated.');
  console.log(temporal ? 'Error: Temporal properties were violated.' : 'Error: Invariant ' + rule.expected + ' is violated.');
  console.log('State 1: <Initial predicate>');
  console.log('/\\\\ admitted = {1}');
  console.log('State 2: <Tap>');
  console.log(mode === 'three-admissions' ? '/\\\\ admitted = {1, 2, 3}' : '/\\\\ admitted = {1, 2}');
  if (mode === 'bad-witness-exit') code = 97;
  if (mode === 'zero-witness-exit') code = 0;
  if (mode === 'swapped-witness-exit') code = temporal ? 12 : 13;
}
console.log('10 states generated, 5 distinct states found, 0 states left on queue.');
console.log('Finished in 00s at (2026-10-14 00:00:00)');
process.exit(code);
`,
    );
    chmodSync(fake, 0o755);
    // Avoid machine load and clock-dependent suite deadlines affecting synthetic results.
    writeFileSync(join(root, "sysctl"), '#!/bin/sh\necho "{ 0 0 0 }"\n');
    chmodSync(join(root, "sysctl"), 0o755);
    const timeout = join(root, "timeout");
    writeFileSync(timeout, '#!/bin/sh\nshift\nshift\nshift\nexec "$@"\n');
    chmodSync(timeout, 0o755);
    const result = spawnSync("sh", [script], {
      cwd: dir,
      env: {
        ...process.env,
        PATH: `${root}:${process.env.PATH ?? ""}`,
        TLC: fake,
        TIMEOUT: timeout,
        PROBE_RULES: JSON.stringify(rules),
        PROBE_INDEX: join(root, "index"),
        PROBE_MODE: mode,
        CHECK_MODE: "bfs",
        TLC_TIMEOUT: "150",
        WORKERS: "1",
        SKIP_MAIN: "0",
        LOG_DIR: "",
        TMPDIR: root,
      },
      encoding: "utf8",
      timeout: 30_000,
      maxBuffer: 2 ** 20,
    });
    assert.ifError(result.error);
    return { status: result.status, output: result.stdout + result.stderr };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

for (const script of scripts) {
  test(`${script}: accepts valid verdicts and rejects corrupt TLC results`, () => {
    const clean = probe(script, "clean");
    assert.equal(clean.status, 0, clean.output);
    for (const mode of [
      "bad-success-exit",
      "bad-witness-exit",
      "wrong-first-property",
      "zero-witness-exit",
      "swapped-witness-exit",
      "error-before-success",
    ]) {
      const result = probe(script, mode);
      assert.notEqual(
        result.status,
        0,
        `${script}: accepted ${mode}\n${result.output}`,
      );
    }
  });
}

test("ButtonTap: rejects a first counterexample with three admissions (T3)", () => {
  const result = probe("button-tap-check.sh", "three-admissions");
  assert.notEqual(result.status, 0, result.output);
});

test("temporal verdict requires exactly one configured property", () => {
  const result = probe("goal-check.sh", "clean", (dir) => {
    for (const name of readdirSync(dir).filter((name) =>
      name.endsWith(".cfg"),
    )) {
      const file = join(dir, name);
      writeFileSync(
        file,
        readFileSync(file, "utf8").replace(
          /^(PROPERT(?:Y|IES)\s+\w+)$/gm,
          "$1 UnexpectedProperty",
        ),
      );
    }
  });
  assert.notEqual(result.status, 0, result.output);
});

test("TLC infrastructure exit codes never become a valid witness from matching text", () => {
  const root = mkdtempSync(join(tmpdir(), "iva-tlc-verdict-"));
  try {
    const log = join(root, "tlc.log");
    const cfg = join(root, "model.cfg");
    writeFileSync(log, "Error: Invariant ExpectedInvariant is violated.\n");
    writeFileSync(cfg, "INVARIANT ExpectedInvariant\n");
    const verdict = (code: number) => {
      const result = spawnSync(
        "sh",
        [
          "-c",
          '. "$1"; tlc_expect "$2" "$3" "$4" ExpectedInvariant',
          "probe",
          fileURLToPath(new URL("../specs/tlc-result.sh", import.meta.url)),
          log,
          String(code),
          cfg,
        ],
        { env: { ...process.env, LOG_DIR: "" } },
      );
      assert.ifError(result.error);
      return result.status;
    };
    assert.equal(verdict(12), 0, "valid witness must reach the actual parser");
    fc.assert(
      fc.property(
        fc
          .integer({ min: 1, max: 255 })
          .filter((code) => code !== 12 && code !== 13),
        (code) => {
          assert.notEqual(
            verdict(code),
            0,
            `accepted infrastructure exit ${code}`,
          );
        },
      ),
      { seed: 20261014, numRuns: 32 },
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
