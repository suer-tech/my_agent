/**
 * One `ExecStart=` argument, quoted the way systemd reads it. `$` and `%` are doubled
 * because systemd expands both before the process ever starts, and a path with a NUL or
 * a newline in it is refused: the unit file is line-based, and a value that breaks the
 * line writes a directive nobody authored.
 */
export function systemdExecArgument(value: string): string {
  if (/[\0\r\n]/u.test(value))
    throw new Error("systemd argument contains NUL or a newline");
  let escaped = "";
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (character === "\\") escaped += "\\\\";
    else if (character === '"') escaped += '\\"';
    else if (character === "$") escaped += "$$";
    else if (character === "%") escaped += "%%";
    else if (code < 0x20 || code === 0x7f)
      escaped += `\\x${code.toString(16).padStart(2, "0")}`;
    else escaped += character;
  }
  return `"${escaped}"`;
}

export function systemdEnvironmentSegment(value: string): string {
  if (/[\0\r\n]/u.test(value))
    throw new Error("systemd environment value contains NUL or a newline");
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll('"', '\\"')
    .replaceAll("%", "%%");
}

function words(value: string, dollars: boolean, home?: string): string[] {
  const result: string[] = [];
  let rest = value.trim();
  while (rest) {
    const match = /^(?:"((?:\\.|[^"\\])*)"|([^\s"]+))(?:\s+|$)/u.exec(rest);
    if (!match) throw new Error(`invalid systemd value: ${value}`);
    let token =
      match[1] === undefined
        ? match[2]
        : match[1].replace(/\\(x[0-9a-f]{2}|[\\"])/giu, (_, escape: string) =>
            escape.startsWith("x")
              ? String.fromCharCode(parseInt(escape.slice(1), 16))
              : escape,
          );
    if (match[1] !== undefined) {
      if (dollars) token = token.replaceAll("$$", "$");
      token = token.replace(/%%|%h/gu, (part) =>
        part === "%%" ? "%" : (home ?? "%h"),
      );
    } else if (home) token = token.replaceAll("%h", home);
    result.push(token);
    rest = rest.slice(match[0].length);
  }
  return result;
}

export const systemdExecWords = (value: string, home?: string): string[] =>
  words(value, true, home);
export const systemdEnvironmentWords = (
  value: string,
  home?: string,
): string[] => words(value, false, home);
