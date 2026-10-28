/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { REVIEW_CASES } from "./plain-shell-command.corpus.test-fixture.ts";
import { schedulerBypassViolation as referenceScheduler } from "./scheduler-bypass-guard.test-fixture.ts";
import { selfRestartViolation as referenceSelfRestart } from "./self-restart-guard.test-fixture.ts";

type Subject = typeof import("./plain-shell-command.ts");

const loadSubject = async (): Promise<Subject> =>
  await import("./plain-shell-command.ts");

const ACCEPTED_DATA = [
  `grep -nE "foo|batch|bar" file | head -20 | cut -d: -f1`,
  `egrep -rl --include=*.ts --exclude=old --exclude-dir=node_modules x .`,
  `fgrep -A 3 -B2 -C1 -m5 -e pattern --color=never file`,
  `echo -nE "a|batch"; echo -e 'a\\nb'`,
  `head -qv -n 20 -c4 file`,
  `cut -s -d: -f 1 -b2 -c 3 file`,
  `wc -lcwmL file`,
  `ls -laAhd1trRSF .`,
  `getent hosts api.telegram.org`,
  `grep -- -n file 2>/dev/null | head -n3 >/dev/null 2>&1`,
  `# ввод из комментария\necho ok`,
  `echo "a|batch";`,
  `grep -nE "at|batch" file;`,
  `getent hosts api.telegram.org;`,
] as const;

const ACCEPTED_HELP = [
  `"iva" restart --help`,
  `iva doctor --help`,
  `iva restart '--help'`,
  `iva restart --he"lp"`,
  `iva plugin sync -h`,
  `iva restart 2>&1 --help`,
  `iva plugin sync --help 2>&1 | head -20; echo ok`,
  `iva restart --help;`,
] as const;

const REJECTED = [
  `printf -v 'x[$(touch /tmp/marker)]' x`,
  `printf -v'x[$(touch /tmp/marker)]' x`,
  `sort -o /tmp/x input`,
  `uniq INPUT OUTPUT`,
  `file -C -m magic`,
  `rg --pre=x pattern input`,
  `tail --follow input`,
  `cat input`,
  `tee output`,
  `grep --output=x pattern input`,
  `grep --include pattern input`,
  `grep --color=always pattern input`,
  `head --lines=3 input`,
  `cut -z input`,
  `ls --all`,
  `getent --help`,
  `"grep" pattern input`,
  `/usr/bin/grep pattern input`,
  `X=1 grep pattern input`,
  `> /dev/null grep pattern input`,
  `grep pattern input >file`,
  `grep pattern <<'EOF'\ninput\nEOF`,
  `grep "a\\|b" input`,
  `grep "$(iva restart)" input`,
  `grep pattern ||| echo ok`,
  `grep pattern &&& echo ok`,
  `grep pattern ;; echo ok`,
  `grep pattern |& head`,
  `grep pattern & head`,
  `grep pattern\rinput`,
  `iva restart >&2 --help`,
  `iva restart --help 2>/dev/null`,
  `a;;`,
  `a;|`,
  `a;&`,
  `;`,
  `echo a;;`,
] as const;

const TRAILING_SEMICOLON_CASES = [
  `echo "a|batch";`,
  `grep -nE "at|batch" file;`,
  `getent hosts api.telegram.org;`,
  `iva restart --help;`,
] as const;

const QUOTED_DATA = new Set([
  "remaining sed pipeline",
  "remaining git commit",
  "remaining quoted iva",
  "review1 absolute grep",
  "review1 relative grep",
  "review1 absolute echo",
  "review1 neighbor sh",
  "review1 neighbor bash",
  "review1 neighbor python",
  "review1 neighbor chmod",
  "review1 neighbor source",
  "review1 neighbor dot",
  "review1 neighbor executable",
  "review1 input process substitution",
  "review1 output process substitution",
  "review2 assignment leading redirect scheduler",
  "review2 assignment leading redirect self",
  "review2 malformed fd scheduler",
  "review2 triple pipe",
  "review2 triple ampersand",
  "review2 triple semicolon",
  "review2 mixed pipe ampersand",
  "review2 quoted assignment scheduler",
  "review2 quoted assignment self",
  "deepseek bash both redirect",
  "deepseek bash append both redirect",
  "specv1 F6 exact",
  "fact7 rg data",
  "fact7 jq data",
  "fact7 sed data",
]);

const SOFTENED = new Set([
  ...QUOTED_DATA,
  "c1 grep pipeline",
  "c1 echo batch",
  "c1 echo at",
  "c1 getent",
  "c1 help sequence",
  "review2 fd before help",
  "deepseek help doctor",
  "deepseek help quoted value",
  "deepseek help concatenated",
  "deepseek help plugin short",
  "specv1 F3 exact",
  "specv1 F4 exact",
  "fact7 comment pipe batch",
  "fact7 quoted greater",
]);

const pair = (
  selfRestart: (input: string) => string | null,
  scheduler: (input: string) => string | null,
  input: string,
): readonly [boolean, boolean] => [
  selfRestart(input) !== null,
  scheduler(input) !== null,
];

test("белый список принимает только заданные команды данных и опции", async () => {
  const { plainDataCommand } = await loadSubject();
  for (const input of ACCEPTED_DATA) {
    assert.equal(plainDataCommand(input), true, input);
  }
  for (const input of REJECTED) {
    assert.equal(plainDataCommand(input), false, input);
  }
});

test("справка iva принимает точное слово help и только дублирование stderr", async () => {
  const { plainDataCommand, plainHelpCommand } = await loadSubject();
  for (const input of ACCEPTED_HELP) {
    assert.equal(plainDataCommand(input), false, input);
    assert.equal(plainHelpCommand(input), true, input);
  }
  for (const input of REJECTED) {
    assert.equal(plainHelpCommand(input), false, input);
  }
});

test("корпус пропускает завершающую точку с запятой через оба гварда", async () => {
  const [{ selfRestartViolation }, { schedulerBypassViolation }] =
    await Promise.all([
      import("./self-restart-guard.ts"),
      import("./scheduler-bypass-guard.ts"),
    ]);
  for (const input of TRAILING_SEMICOLON_CASES) {
    assert.deepEqual(
      pair(selfRestartViolation, schedulerBypassViolation, input),
      [false, false],
      input,
    );
  }
});

test("корпус четырёх рецензий сохраняет исполняемые отказы и разрешает данные 287-A", async () => {
  const [{ selfRestartViolation }, { schedulerBypassViolation }] =
    await Promise.all([
      import("./self-restart-guard.ts"),
      import("./scheduler-bypass-guard.ts"),
    ]);

  for (const { name, input, expected: historical } of REVIEW_CASES) {
    const expected = QUOTED_DATA.has(name) ? [false, false] : historical;
    const actual = pair(selfRestartViolation, schedulerBypassViolation, input);
    assert.deepEqual(actual, expected, `${name}: ${JSON.stringify(input)}`);
    const baseline = pair(referenceSelfRestart, referenceScheduler, input);
    if (SOFTENED.has(name)) {
      assert.notDeepEqual(baseline, expected, `${name}: ожидалось смягчение`);
    } else {
      assert.deepEqual(
        baseline,
        expected,
        `${name}: эталонный вердикт изменён`,
      );
    }
  }
});

test("эталонные гварды побайтно соответствуют снимку ef7966cd", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const expected = new Map([
    [
      "self-restart-guard.test-fixture.ts",
      "fa0a276fce58668ed58ea9472a2d7078801ab3be923d13ec8885c8ec2dfefd3b",
    ],
    [
      "scheduler-bypass-guard.test-fixture.ts",
      "1d5db2b5d2374d2420d88201816a7a8e008d67212718c6b9d1ecd2c10110261e",
    ],
  ]);
  for (const [name, digest] of expected) {
    const actual = createHash("sha256")
      .update(
        readFileSync(join(here, name), "utf8").replace(
          /import\s*\{\s*commandSegments,\s*type CommandPosition,?\s*\}\s*from "\.\/self-restart-guard\.test-fixture\.ts";/,
          'import { commandSegments, type CommandPosition } from "./self-restart-guard.ts";',
        ),
      )
      .digest("hex");
    assert.equal(actual, digest, name);
  }
});

await test("prototype names are outside the plain command table", async (t) => {
  const { plainDataCommand, plainHelpCommand } = await loadSubject();
  for (const name of [
    "constructor",
    "toString",
    "valueOf",
    "__proto__",
    "hasOwnProperty",
    "isPrototypeOf",
    "propertyIsEnumerable",
    "toLocaleString",
    "__defineGetter__",
    "__defineSetter__",
    "__lookupGetter__",
    "__lookupSetter__",
  ]) {
    for (const command of [
      name,
      `${name} x`,
      `${name} -x`,
      `${name} -n 1`,
      `iva --help | ${name}`,
    ]) {
      await t.test(command, () => {
        assert.equal(plainDataCommand(command), false, command);
        assert.equal(plainHelpCommand(command), false, command);
      });
    }
  }
});
await test("shell guards always decide for arbitrary input (seed 101307)", async () => {
  const fc = await import("fast-check");
  const { plainDataCommand, plainHelpCommand } = await loadSubject();
  const { selfRestartViolation } = await import("./self-restart-guard.ts");
  const { schedulerBypassViolation } =
    await import("./scheduler-bypass-guard.ts");
  fc.default.assert(
    fc.default.property(
      fc.default.oneof(
        fc.default.string(),
        fc.default.constantFrom(
          "constructor -x",
          "toString -n 1",
          "__proto__ -x",
        ),
      ),
      (input) => {
        assert.equal(typeof plainDataCommand(input), "boolean");
        assert.equal(typeof plainHelpCommand(input), "boolean");
        assert.doesNotThrow(() => selfRestartViolation(input));
        assert.doesNotThrow(() => schedulerBypassViolation(input));
      },
    ),
    { seed: 101307, numRuns: 1000 },
  );
});
