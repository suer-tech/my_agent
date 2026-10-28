import assert from "node:assert/strict";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import fc from "fast-check";

import { customOverlay } from "./version-update.ts";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const SCRATCH = join(ROOT, ".scratch/work/evidence/fix-pbt-codex");
const PERMISSIONS_SEED = 2_026_091_201;

void test(`PROPERTY: unreadable data/custom fails clearly (seed ${PERMISSIONS_SEED})`, () => {
  mkdirSync(SCRATCH, { recursive: true });
  let serial = 0;
  fc.assert(
    fc.property(
      fc.constantFrom("directory", "file"),
      fc.constantFrom(0o000, 0o200),
      (blocked, mode) => {
        const root = mkdtempSync(join(SCRATCH, `permissions-${serial++}-`));
        const custom = join(root, "data/custom");
        const file = join(custom, "agent/tools/mine.ts");
        mkdirSync(join(custom, "agent/tools"), { recursive: true });
        writeFileSync(file, "export const mine = 1;\n", { mode: 0o600 });
        const unreadable = blocked === "directory" ? custom : file;
        chmodSync(unreadable, mode);
        try {
          assert.throws(
            () => customOverlay(custom),
            (error: unknown) => {
              assert.ok(error instanceof Error);
              assert.match(
                error.message,
                /^cannot read Custom layer .+: EACCES:/u,
              );
              assert.equal(
                (error.cause as NodeJS.ErrnoException | undefined)?.code,
                "EACCES",
              );
              return true;
            },
          );
        } finally {
          chmodSync(unreadable, blocked === "directory" ? 0o700 : 0o600);
          rmSync(root, { recursive: true, force: true });
        }
      },
    ),
    {
      seed: PERMISSIONS_SEED,
      numRuns: 20,
      verbose: true,
      examples: [
        ["directory", 0o000],
        ["file", 0o000],
      ],
    },
  );
});
