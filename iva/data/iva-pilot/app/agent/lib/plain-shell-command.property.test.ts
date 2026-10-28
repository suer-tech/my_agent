/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// КАК ВОСПРОИЗВЕСТИ: seed печатается до загрузки проверяемого файла; fast-check
// добавляет path минимального контрпримера в сообщение об ошибке.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import fc from "fast-check";
import { parse } from "just-bash";
import { schedulerBypassViolation as referenceScheduler } from "./scheduler-bypass-guard.test-fixture.ts";
import { selfRestartViolation as referenceSelfRestart } from "./self-restart-guard.test-fixture.ts";

const SEED = 287_041;
console.info(`L11 property seed: ${SEED}`);

type Word = { readonly value: string; readonly bare: boolean };
type Command = {
  readonly words: readonly Word[];
  readonly redirects: readonly string[];
};
type Policy = {
  readonly plain: string;
  readonly valued: string;
  readonly long?: readonly string[];
  readonly numeric?: boolean;
};
type ParseState = {
  readonly commands: Command[];
  readonly words: Word[];
  readonly redirects: string[];
  readonly parts: string[];
  quoted: boolean;
};

const POLICIES: Readonly<Record<string, Policy>> = {
  grep: {
    plain: "nEFGorRlLivchHwx sqa".replaceAll(" ", ""),
    valued: "ABCme",
    long: ["--include=", "--exclude=", "--exclude-dir=", "--color=never"],
  },
  egrep: {
    plain: "nEFGorRlLivchHwx sqa".replaceAll(" ", ""),
    valued: "ABCme",
    long: ["--include=", "--exclude=", "--exclude-dir=", "--color=never"],
  },
  fgrep: {
    plain: "nEFGorRlLivchHwx sqa".replaceAll(" ", ""),
    valued: "ABCme",
    long: ["--include=", "--exclude=", "--exclude-dir=", "--color=never"],
  },
  echo: { plain: "neE", valued: "" },
  head: { plain: "qv", valued: "nc", numeric: true },
  cut: { plain: "s", valued: "cdfb" },
  wc: { plain: "lcwmL", valued: "" },
  ls: { plain: "laAhd1trRSF", valued: "" },
  getent: { plain: "", valued: "" },
};

const TOKEN =
  /2>\/dev\/null|>\/dev\/null|2>&1|<<|#[^\n]*|'[^']*'|"[^"\\$`]*"|[A-Za-z0-9_./:=,@%+~^*-]+|[ \t]+|\n|;|\|\||\||&&/gy;
const REDIRECTS = new Set(["2>/dev/null", ">/dev/null", "2>&1"]);

function lex(input: string): string[] | null {
  const out: string[] = [];
  TOKEN.lastIndex = 0;
  while (TOKEN.lastIndex < input.length) {
    const match = TOKEN.exec(input);
    if (!match) return null;
    out.push(match[0]);
  }
  return out;
}

function finishWord(state: ParseState): void {
  if (state.parts.length === 0) return;
  state.words.push({ value: state.parts.join(""), bare: !state.quoted });
  state.parts.length = 0;
  state.quoted = false;
}

function finishCommand(state: ParseState): boolean {
  if (state.words.length === 0) return false;
  state.commands.push({
    words: [...state.words],
    redirects: [...state.redirects],
  });
  state.words.length = 0;
  state.redirects.length = 0;
  return true;
}

function wordPart(piece: string): { value: string; quoted: boolean } | null {
  if (piece.startsWith("'") || piece.startsWith('"')) {
    return { value: piece.slice(1, -1), quoted: true };
  }
  return /^[A-Za-z0-9_./:=,@%+~^*-]+$/.test(piece)
    ? { value: piece, quoted: false }
    : null;
}

function takePiece(state: ParseState, piece: string): boolean {
  const part = wordPart(piece);
  if (part) {
    state.parts.push(part.value);
    state.quoted ||= part.quoted;
    return true;
  }
  if (piece.startsWith("#")) return state.parts.length === 0;
  finishWord(state);
  if (/^[ \t]+$/.test(piece)) return true;
  if (piece === "<<") return false;
  if (REDIRECTS.has(piece)) {
    if (state.words.length === 0) return false;
    state.redirects.push(piece);
    return true;
  }
  if (piece === "\n" && state.words.length === 0) return true;
  return finishCommand(state);
}

function parsedCommands(input: string): Command[] | null {
  const pieces = lex(input);
  if (!pieces) return null;
  const state: ParseState = {
    commands: [],
    words: [],
    redirects: [],
    parts: [],
    quoted: false,
  };
  for (const piece of pieces) {
    if (!takePiece(state, piece)) return null;
  }
  finishWord(state);
  if (state.words.length > 0) finishCommand(state);
  return state.commands.length > 0 ? state.commands : null;
}

function shortOptionArity(value: string, policy: Policy): 0 | 1 | null {
  if (policy.numeric && /^-\d+$/.test(value)) return 0;
  const letters = value.slice(1);
  const valued = [...letters].findIndex(
    (letter) => !policy.plain.includes(letter),
  );
  if (valued < 0) return letters.length > 0 ? 0 : null;
  const valuedLetter = letters[valued];
  if (!valuedLetter || !policy.valued.includes(valuedLetter)) return null;
  return valued === letters.length - 1 ? 1 : 0;
}

function optionArity(value: string, policy: Policy): -1 | 0 | 1 | null {
  if (value === "--") return -1;
  if (value === "-" || !value.startsWith("-")) return 0;
  if (!value.startsWith("--")) return shortOptionArity(value, policy);
  const allowed = (policy.long ?? []).some((option) =>
    option.endsWith("=") ? value.startsWith(option) : value === option,
  );
  return allowed ? 0 : null;
}

function optionsAllowed(words: readonly Word[], policy: Policy): boolean {
  for (let index = 1; index < words.length; index += 1) {
    const arity = optionArity(words[index]?.value ?? "", policy);
    if (arity === -1) return true;
    if (arity === null || (arity === 1 && ++index >= words.length))
      return false;
  }
  return true;
}

function dataCommand(command: Command): boolean {
  const first = command.words[0];
  if (!first?.bare || first.value.includes("/") || first.value.includes("="))
    return false;
  const policy = POLICIES[first.value];
  return policy !== undefined && optionsAllowed(command.words, policy);
}

function helpCommand(command: Command): boolean {
  const first = command.words[0];
  return (
    first?.value === "iva" &&
    command.redirects.every((redirect) => redirect === "2>&1") &&
    command.words
      .slice(1)
      .some(({ value }) => value === "--help" || value === "-h")
  );
}

function independentlyAllowed(input: string): boolean {
  const commands = parsedCommands(input);
  return (
    commands !== null &&
    commands.every((command) => dataCommand(command) || helpCommand(command))
  );
}

// The additional 287-A class has quoted data and no executable guard target.
// The existing Bash parser identifies commands independently of the guard scanner.
function independentlyParsedVerdict(input: string): readonly boolean[] | null {
  const words: string[] = [];
  function literal(node: unknown): string {
    if (!node || typeof node !== "object") return "";
    const part = node as Record<string, unknown>;
    if (typeof part.value === "string")
      return part.value.replace(/[;|()`\n&<>]/g, "?");
    if (Array.isArray(part.parts)) return part.parts.map(literal).join("");
    return "?";
  }
  function visit(node: unknown): void {
    if (!node || typeof node !== "object") return;
    const part = node as Record<string, unknown>;
    if (part.type === "SimpleCommand") {
      words.push(
        [
          literal(part.name).replace(/\s/g, "?"),
          ...(part.args as unknown[]).map(literal),
        ].join(" "),
      );
    }
    for (const value of Object.values(part))
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === "object") visit(value);
  }
  try {
    visit(parse(input));
    return [
      referenceSelfRestart(words.join("; ")) !== null,
      referenceScheduler(words.join("; ")) !== null,
    ];
  } catch {
    return spawnSync("/bin/bash", ["-n", "-c", input], { encoding: "utf8" })
      .status !== 0
      ? [false, false]
      : null;
  }
}

function independentlyQuotedData(
  input: string,
  before: readonly boolean[],
  after: readonly boolean[],
): boolean {
  const parsed = independentlyParsedVerdict(input);
  return (
    /['"]/.test(input) &&
    parsed !== null &&
    parsed.every(
      (blocked, index) => !(before[index] && !after[index]) || !blocked,
    )
  );
}

function independentlyBlocked(
  input: string,
  before: readonly boolean[],
  after: readonly boolean[],
): boolean {
  const parsed = independentlyParsedVerdict(input);
  return (
    parsed !== null &&
    parsed.every(
      (blocked, index) => !(!before[index] && after[index]) || blocked,
    )
  );
}

const ALL_OPTIONS = [
  "-n",
  "-E",
  "-F",
  "-G",
  "-o",
  "-r",
  "-R",
  "-l",
  "-L",
  "-i",
  "-v",
  "-c",
  "-h",
  "-H",
  "-w",
  "-x",
  "-s",
  "-q",
  "-a",
  "-A",
  "-A3",
  "-B",
  "-B2",
  "-C",
  "-C1",
  "-m",
  "-m5",
  "-e",
  "-epattern",
  "--include=*.ts",
  "--exclude=x",
  "--exclude-dir=node_modules",
  "--color=never",
  "-e",
  "-q",
  "-v",
  "-n",
  "-c",
  "-20",
  "-d",
  "-f",
  "-b",
  "-w",
  "-L",
  "-1",
  "-t",
  "-S",
] as const;

const DANGEROUS = [
  "-v",
  "-o",
  "--output",
  "-C",
  "-S",
  "-z",
  "--pre",
  "--pre=x",
  "--comp=x",
  "-f",
  "-F",
  "--follow",
  "printf",
  "sort",
  "uniq",
  "file",
  "rg",
  "jq",
  "tail",
  "cat",
  "tee",
] as const;

const TOKEN_DICTIONARY = [
  "grep",
  "egrep",
  "fgrep",
  "echo",
  "head",
  "cut",
  "wc",
  "ls",
  "getent",
  ...ALL_OPTIONS,
  ...DANGEROUS,
  "sh",
  "bash",
  "node",
  "python3",
  "xargs",
  "env",
  "sudo",
  "eval",
  "time",
  "sed",
  "git",
  "./grep",
  "/tmp/grep",
  ";",
  "|",
  "||",
  "&&",
  "|||",
  "&&&",
  ";;",
  "|&",
  "&",
  "(",
  ")",
  "{",
  "}",
  "$",
  "${X}",
  "$(",
  "`",
  "\\",
  "\\\n",
  "<(",
  ">(",
  "<<<",
  "<<'EOF'",
  ">",
  ">>",
  "2>",
  "2>&1",
  "2>&1x",
  ">/dev/null",
  ">file",
  "X=1",
  '"X=1"',
  "\r",
  "\v",
  "\f",
  "\u00a0",
  "# comment",
  "'iva restart'",
  '"a|batch"',
  "'a|at b'",
  "iva restart",
  "systemctl --user restart iva",
  "pkill node",
  "crontab -",
  "at now",
  "batch",
  "systemd-run --user true",
  "api.telegram.org",
  "~/.iva-scripts/x",
] as const;

const VALID_INPUTS = [
  `grep -nE "foo|batch|bar" file | head -20 | cut -d: -f1`,
  `grep -oE '(FOO|BAR)=' .env; ls -la`,
  `grep -n -E "EnvironmentFile|Environment=" unit.service`,
  `echo "a|batch"`,
  `echo 'a|at b'`,
  `getent hosts api.telegram.org`,
  `iva plugin sync --help 2>&1 | head -20`,
  `iva doctor --help 2>&1 | head -20`,
  `wc -lc file`,
  `ls -la`,
  `iva restart`,
] as const;

const dictionaryToken = fc.constantFrom(...TOKEN_DICTIONARY);
const barePart = fc.constantFrom("pre", "left", "x", "right", "post");
const quotedToken = fc.oneof(
  dictionaryToken.map((content) => `'${content}'`),
  dictionaryToken.map((content) => `"${content}"`),
);
const gluedWord = fc.oneof(
  fc
    .tuple(barePart, dictionaryToken, barePart)
    .map(([left, content, right]) => `${left}'${content}'${right}`),
  fc
    .tuple(barePart, dictionaryToken, barePart)
    .map(([left, content, right]) => `${left}"${content}"${right}`),
);
const generatedToken = fc.oneof(dictionaryToken, quotedToken, gluedWord);
const randomTokens = fc
  .array(generatedToken, { minLength: 1, maxLength: 18 })
  .map((tokens) => tokens.join(" "));
const inputArbitrary = fc.oneof(
  { weight: 3, arbitrary: fc.constantFrom(...VALID_INPUTS) },
  { weight: 7, arbitrary: randomTokens },
);

function verdict(
  selfRestart: (input: string) => string | null,
  scheduler: (input: string) => string | null,
  input: string,
): readonly [boolean, boolean] {
  return [selfRestart(input) !== null, scheduler(input) !== null];
}

function direction(
  before: readonly [boolean, boolean],
  after: readonly [boolean, boolean],
): "мягче" | "строже" | "равно" {
  const softer = before.some((blocked, index) => blocked && !after[index]);
  const stricter = after.some((blocked, index) => blocked && !before[index]);
  if (stricter) return "строже";
  return softer ? "мягче" : "равно";
}

async function candidate() {
  const [plain, self, scheduler] = await Promise.all([
    import("./plain-shell-command.ts"),
    import("./self-restart-guard.ts"),
    import("./scheduler-bypass-guard.ts"),
  ]);
  return { ...plain, ...self, ...scheduler };
}

test(`P3: вся принятая строка подтверждена независимым разбором (seed ${SEED})`, async () => {
  const { plainDataCommand, plainHelpCommand } = await candidate();
  fc.assert(
    fc.property(inputArbitrary, (input) => {
      if (!plainDataCommand(input) && !plainHelpCommand(input)) return;
      assert.equal(independentlyAllowed(input), true, JSON.stringify(input));
    }),
    { seed: SEED, numRuns: 3_000 },
  );
});

test("P3: heredoc запрещён только вне кавычек", async () => {
  const { plainDataCommand } = await candidate();
  assert.equal(plainDataCommand("echo '<<'"), true);
  assert.equal(independentlyAllowed("echo '<<'"), true);
  assert.equal(plainDataCommand(`echo <<'EOF'`), false);
  assert.equal(independentlyAllowed(`echo <<'EOF'`), false);
});

test(`P4: каждое изменение подтверждено независимым разбором (seed ${SEED})`, async () => {
  const subject = await candidate();
  fc.assert(
    fc.property(inputArbitrary, (input) => {
      const before = verdict(referenceSelfRestart, referenceScheduler, input);
      const after = verdict(
        subject.selfRestartViolation,
        subject.schedulerBypassViolation,
        input,
      );
      const change = direction(before, after);
      if (change === "строже")
        assert.equal(
          independentlyBlocked(input, before, after),
          true,
          JSON.stringify(input),
        );
      if (change === "мягче") {
        assert.equal(
          subject.plainDataCommand(input) ||
            subject.plainHelpCommand(input) ||
            independentlyQuotedData(input, before, after),
          true,
          JSON.stringify(input),
        );
      }
    }),
    { seed: SEED, numRuns: 3_000 },
  );
});

test(`P4 10 000 входов сохраняет числа и корпус отличий (seed ${SEED})`, async () => {
  const subject = await candidate();
  const counts = { мягче: 0, строже: 0, равно: 0 };
  const differences = new Map<string, "data" | "help" | "quoted data">();
  for (const input of fc.sample(inputArbitrary, {
    seed: SEED,
    numRuns: 10_000,
  })) {
    const before = verdict(referenceSelfRestart, referenceScheduler, input);
    const after = verdict(
      subject.selfRestartViolation,
      subject.schedulerBypassViolation,
      input,
    );
    const change = direction(before, after);
    counts[change] += 1;
    if (change === "строже")
      assert.equal(
        independentlyBlocked(input, before, after),
        true,
        JSON.stringify(input),
      );
    if (change === "мягче") {
      const data = subject.plainDataCommand(input);
      const help = subject.plainHelpCommand(input);
      const quoted = independentlyQuotedData(input, before, after);
      assert.equal(data || help || quoted, true, JSON.stringify(input));
      const kind = data
        ? "data"
        : help
          ? "help"
          : quoted
            ? "quoted data"
            : undefined;
      assert.notEqual(kind, undefined, JSON.stringify(input));
      if (kind === undefined) continue;
      differences.set(input, kind);
    }
  }
  console.info(
    `L11 P4: мягче=${counts.мягче} строже=${counts.строже} равно=${counts.равно}`,
  );
  for (const [input, kind] of differences) {
    console.info(
      `L11 P4 difference: ${JSON.stringify({ input, class: kind })}`,
    );
  }
  assert.ok(counts.мягче > 0);
});
