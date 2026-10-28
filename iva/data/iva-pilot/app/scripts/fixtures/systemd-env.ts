// In-process Linux CLI tests inject control methods rather than spawning a fake
// systemctl. Select the same Linux branch explicitly on macOS as their child fixtures do.
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "iva-systemd-env-"));
const prior = process.env.PATH;
writeFileSync(
  join(dir, "systemctl"),
  "#!/bin/sh\necho 'systemctl must be injected in this test' >&2\nexit 97\n",
  { mode: 0o755 },
);
process.env.PATH = `${dir}:${prior ?? ""}`;
process.once("exit", () => {
  if (prior === undefined) delete process.env.PATH;
  else process.env.PATH = prior;
  rmSync(dir, { recursive: true, force: true });
});
