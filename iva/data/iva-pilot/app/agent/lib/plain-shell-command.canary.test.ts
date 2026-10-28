/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const SHELLS = ["/bin/bash", "/bin/dash"] as const;

for (const shell of SHELLS) {
  test(
    `canary ${shell}: printf -v не получает ранний пропуск`,
    { skip: existsSync(shell) ? false : `${shell} отсутствует` },
    async (t) => {
      const { plainDataCommand, plainHelpCommand } =
        await import("./plain-shell-command.ts");
      const dir = mkdtempSync(join(tmpdir(), "iva-plain-command-canary-"));
      t.after(() => rmSync(dir, { recursive: true, force: true }));
      const marker = join(dir, "executed");
      const input = `printf -v 'x[$(touch ${marker})]' x`;
      const allowed = plainDataCommand(input) || plainHelpCommand(input);
      if (allowed) spawnSync(shell, ["-c", input]);
      assert.equal(allowed, false);
      assert.equal(existsSync(marker), false, "printf -v исполнил подстановку");
    },
  );

  test(
    `canary ${shell}: iva с help получает только аргументы справки`,
    { skip: existsSync(shell) ? false : `${shell} отсутствует` },
    async (t) => {
      const { plainHelpCommand } = await import("./plain-shell-command.ts");
      const dir = mkdtempSync(join(tmpdir(), "iva-help-canary-"));
      t.after(() => rmSync(dir, { recursive: true, force: true }));
      const log = join(dir, "argv");
      const stub = join(dir, "iva");
      writeFileSync(
        stub,
        `#!/bin/sh\nprintf '<call>\\n' >> "$IVA_ARGV_LOG"\nprintf '%s\\n' "$@" >> "$IVA_ARGV_LOG"\n`,
      );
      chmodSync(stub, 0o755);
      const input = "iva restart 2>&1 --help";
      assert.equal(plainHelpCommand(input), true);
      const result = spawnSync(shell, ["-c", input], {
        env: {
          ...process.env,
          IVA_ARGV_LOG: log,
          PATH: `${dir}:${process.env.PATH ?? ""}`,
        },
        encoding: "utf8",
      });
      assert.equal(result.status, 0, result.stderr);
      assert.deepEqual(readFileSync(log, "utf8").trimEnd().split("\n"), [
        "<call>",
        "restart",
        "--help",
      ]);
    },
  );
}
