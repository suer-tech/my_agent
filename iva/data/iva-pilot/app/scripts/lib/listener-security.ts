/** Classify listeners for one TCP port from `ss -H -ltn` output. */
export function classifyAgentListeners(
  output: unknown,
  port: string | number,
): "absent" | "loopback" | "exposed" {
  const suffix = `:${port}`;
  const addresses = String(output)
    .split("\n")
    .map((line) => line.trim().split(/\s+/)[3] || "")
    .filter((address) => address.endsWith(suffix));

  if (addresses.length === 0) return "absent";
  const loopback = addresses.every((address) => {
    const host = address.slice(0, -suffix.length);
    return (
      host === "localhost" ||
      host === "[::1]" ||
      /^127(?:\.\d{1,3}){3}$/.test(host)
    );
  });
  return loopback ? "loopback" : "exposed";
}

export function classifyLsofListeners(
  output: unknown,
  port: string | number,
): "absent" | "loopback" | "exposed" {
  const lines = String(output).trim().split("\n").filter(Boolean);
  if (!lines.length || (lines.length === 1 && lines[0].startsWith("COMMAND ")))
    return "absent";
  const listeners = lines.filter((line) => /\(LISTEN\)\s*$/u.test(line));
  if (!listeners.length) return "exposed";
  return listeners.every((line) => {
    const address = /\bTCP (\S+) \(LISTEN\)/u.exec(line)?.[1];
    if (!address?.endsWith(`:${port}`)) return false;
    const host = address.slice(0, -String(port).length - 1);
    return (
      host === "[::1]" ||
      host === "localhost" ||
      /^127(?:\.\d{1,3}){3}$/u.test(host)
    );
  })
    ? "loopback"
    : "exposed";
}
