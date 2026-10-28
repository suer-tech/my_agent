/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import test from "node:test";

const MIB = 1024 * 1024;
const PIECES = [
  "|||",
  "&&&",
  "${X}",
  "$(iva restart)",
  "`pkill node`",
  "\\\n",
  "2>&1x",
  "printf -v",
  "sort --comp=x",
  "systemd-run --user true",
  "at now",
  "batch",
] as const;
const TOKEN_STREAM = [
  `grep -nE "a|batch" file 2>/dev/null | head -n 5 2>&1;\n`,
  `cut -d: -f1 file >/dev/null && wc -lc file;\n`,
  `ls -la || getent hosts api.telegram.org;\n`,
  `echo pre'|'post "a|batch";\n`,
] as const;

function randomInput(bytes: number): string {
  const chunks = ["echo '"];
  let length = chunks[0]?.length ?? 0;
  let state = 287_041;
  while (length < bytes) {
    state = (state * 48_271) % 2_147_483_647;
    const piece = PIECES[state % PIECES.length] ?? "x";
    chunks.push(piece, " ");
    length += piece.length + 1;
  }
  chunks.push("'");
  return chunks.join("");
}

function median(values: number[]): number {
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.floor(ordered.length / 2)] ?? Number.POSITIVE_INFINITY;
}

function tokenStream(bytes: number, rejected: boolean): string {
  const suffix = rejected ? `echo 'printf -v' "sort --comp=x" |&` : "echo ok;";
  const chunks: string[] = [];
  let length = suffix.length;
  let index = 0;
  while (length + (TOKEN_STREAM[index]?.length ?? 0) <= bytes) {
    const piece = TOKEN_STREAM[index] ?? "echo x;\n";
    chunks.push(piece);
    length += piece.length;
    index = (index + 1) % TOKEN_STREAM.length;
  }
  chunks.push(" ".repeat(bytes - length), suffix);
  return chunks.join("");
}

function duration(
  input: string,
  parse: (value: string) => boolean,
  expected = true,
): number {
  const started = performance.now();
  assert.equal(parse(input), expected);
  return performance.now() - started;
}

function linearRatio(
  one: string,
  two: string,
  parse: (value: string) => boolean,
  expected: boolean,
): readonly [number, number, number] {
  duration(one, parse, expected);
  duration(two, parse, expected);
  const oneSamples: number[] = [];
  const twoSamples: number[] = [];
  for (let index = 0; index < 5; index += 1) {
    oneSamples.push(duration(one, parse, expected));
    twoSamples.push(duration(two, parse, expected));
  }
  const oneMs = median(oneSamples);
  const twoMs = median(twoSamples);
  return [oneMs, twoMs, twoMs / Math.max(oneMs, 0.01)];
}

test("разбор случайного мегабайта остаётся линейным", async () => {
  const { plainDataCommand } = await import("./plain-shell-command.ts");
  const one = randomInput(MIB);
  const two = randomInput(2 * MIB);
  plainDataCommand(randomInput(64 * 1024));
  const oneMs = median(
    Array.from({ length: 3 }, () => duration(one, plainDataCommand)),
  );
  const twoMs = median(
    Array.from({ length: 3 }, () => duration(two, plainDataCommand)),
  );
  const ratio = twoMs / Math.max(oneMs, 0.01);
  console.info(
    `L11 linearity: 1MiB=${oneMs.toFixed(2)}ms 2MiB=${twoMs.toFixed(2)}ms ratio=${ratio.toFixed(2)}`,
  );
  assert.ok(ratio <= 3.1, `отношение ${ratio.toFixed(2)} превышает 3.1`);
});

for (const rejected of [false, true] as const) {
  const name = rejected ? "отвергаемого мусора" : "принимаемого входа";
  test(`разбор токенного потока ${name} остаётся линейным`, async () => {
    const { plainDataCommand } = await import("./plain-shell-command.ts");
    const one = tokenStream(MIB, rejected);
    const two = tokenStream(2 * MIB, rejected);
    assert.equal(one.length, MIB);
    assert.equal(two.length, 2 * MIB);
    plainDataCommand(tokenStream(64 * 1024, rejected));
    const [oneMs, twoMs, ratio] = linearRatio(
      one,
      two,
      plainDataCommand,
      !rejected,
    );
    console.info(
      `L11 token linearity (${name}): 1MiB=${oneMs.toFixed(2)}ms 2MiB=${twoMs.toFixed(2)}ms ratio=${ratio.toFixed(2)}`,
    );
    assert.ok(ratio <= 3.1, `отношение ${ratio.toFixed(2)} превышает 3.1`);
  });
}
