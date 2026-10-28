// Узкий белый список shell-входа: истина означает, что каждая команда и каждая
// её опция перечислены ниже, а shell-синтаксис не умеет исполнять скрытый код.
type Word = { readonly value: string; readonly bare: boolean };
type Command = readonly [words: readonly Word[], redirects: readonly string[]];
type Policy = readonly [string, string, (readonly string[])?, boolean?];
// prettier-ignore
type ParseState = { readonly commands: Command[]; readonly words: Word[]; readonly redirects: string[]; required: boolean };
// prettier-ignore
const GREP: Policy = ["nEFGorRlLivchHwxsqa", "ABCme", ["--include=", "--exclude=", "--exclude-dir=", "--color=never"]];
const POLICIES: Readonly<Record<string, Policy>> = {
  grep: GREP,
  egrep: GREP,
  fgrep: GREP,
  echo: ["neE", ""],
  head: ["qv", "nc", [], true],
  cut: ["s", "cdfb"],
  wc: ["lcwmL", ""],
  ls: ["laAhd1trRSF", ""],
  getent: ["", ""],
};
Object.setPrototypeOf(POLICIES, null);
const REDIRECTS = new Set(["2>/dev/null", ">/dev/null", "2>&1"]);
const SEPARATORS = new Set([";", "|", "||", "&&"]);
const TOKEN =
  /2>\/dev\/null|>\/dev\/null|2>&1|(?:[A-Za-z0-9_./:=,@%+~^*-]|'[^']*'|"[^"\\$`]*")+|#[^\n]*|[ \t]+|\n|;|\|\||\||&&/gy;
function boundary(char: string | undefined): boolean {
  return char === undefined || /[ \t\n;|&]/.test(char);
}
function pushCommand(state: ParseState): void {
  state.commands.push([[...state.words], [...state.redirects]]);
  state.words.length = 0;
  state.redirects.length = 0;
}
function decodedWord(raw: string): Word {
  return {
    value: raw.replace(/'([^']*)'|"([^"]*)"/g, "$1$2"),
    bare: !/['"]/.test(raw),
  };
}
function takeSeparator(state: ParseState, raw: string, next: string): boolean {
  if (state.words.length === 0 || (next !== "" && ";|&".includes(next)))
    return false;
  pushCommand(state);
  state.required = raw !== ";";
  return true;
}
// prettier-ignore
function validRedirect(state: ParseState, input: string, start: number, end: number): boolean {
  return state.words.length > 0 && boundary(input[start - 1]) && boundary(input[end]);
}
// prettier-ignore
function takeToken(state: ParseState, input: string, raw: string, start: number, end: number): boolean {
  if (/^[ \t]+$/.test(raw)) return true;
  if (raw.startsWith("#")) return boundary(input[start - 1]);
  if (raw === "\n") {
    if (state.words.length > 0) pushCommand(state);
    return true;
  }
  if (REDIRECTS.has(raw)) {
    if (!validRedirect(state, input, start, end)) return false;
    state.redirects.push(raw);
    return true;
  }
  if (SEPARATORS.has(raw)) return takeSeparator(state, raw, input[end] ?? "");
  state.words.push(decodedWord(raw));
  state.required = false;
  return true;
}
function parse(input: string): Command[] | null {
  // prettier-ignore
  const state: ParseState = { commands: [], words: [], redirects: [], required: false };
  let cursor = 0;
  while (cursor < input.length) {
    TOKEN.lastIndex = cursor;
    const match = TOKEN.exec(input);
    if (!match || match.index !== cursor) return null;
    const raw = match[0];
    const start = cursor;
    cursor += raw.length;
    if (!takeToken(state, input, raw, start, cursor)) return null;
  }
  if (state.required) return null;
  if (state.words.length > 0) pushCommand(state);
  return state.commands.length > 0 ? state.commands : null;
}
function shortNeedsValue(value: string, policy: Policy): boolean | null {
  if (policy[3] && /^-\d+$/.test(value)) return false;
  const letters = value.slice(1);
  for (let index = 0; index < letters.length; index += 1) {
    const letter = letters[index];
    if (!letter) return null;
    if (policy[0].includes(letter)) continue;
    if (policy[1].includes(letter)) return index === letters.length - 1;
    return null;
  }
  return letters.length > 0 ? false : null;
}
function optionArity(value: string, policy: Policy): -1 | 0 | 1 | null {
  if (value === "--") return -1;
  if (value === "-" || !value.startsWith("-")) return 0;
  if (value.startsWith("--")) {
    const allowed = (policy[2] ?? []).some((rule) =>
      rule.endsWith("=") ? value.startsWith(rule) : value === rule,
    );
    return allowed ? 0 : null;
  }
  const needsValue = shortNeedsValue(value, policy);
  if (needsValue === null) return null;
  return needsValue ? 1 : 0;
}
function optionsAllowed(words: readonly Word[], policy: Policy): boolean {
  let ended = false;
  for (let index = 1; index < words.length; index += 1) {
    if (ended) continue;
    const arity = optionArity(words[index]?.value ?? "", policy);
    if (arity === null || (arity === 1 && ++index >= words.length))
      return false;
    if (arity === -1) {
      ended = true;
    }
  }
  return true;
}
function dataCommand(command: Command): boolean {
  const first = command[0][0];
  const policy = first?.bare ? POLICIES[first.value] : undefined;
  return policy !== undefined && optionsAllowed(command[0], policy);
}
function helpCommand(command: Command): boolean {
  const first = command[0][0];
  if (first?.value !== "iva") return false;
  if (command[1].some((item) => item !== "2>&1")) return false;
  return command[0]
    .slice(1)
    .some(({ value }) => value === "--help" || value === "-h");
}
export function plainDataCommand(input: string): boolean {
  const commands = parse(input);
  return commands !== null && commands.every(dataCommand);
}
export function plainHelpCommand(input: string): boolean {
  const commands = parse(input);
  if (!commands?.some(helpCommand)) return false;
  return commands.every(
    (command) => dataCommand(command) || helpCommand(command),
  );
}
