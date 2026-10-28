/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns registration. */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import * as constants from "../agent/lib/helper-store.ts";
test("English and Russian helper limit tables match every exported constant", async () => {
  const expected = Object.entries(constants).filter(([name]) =>
    name.startsWith("HELPER_"),
  );
  assert.equal(expected.length, 10);
  for (const file of ["../docs/helpers.md", "../docs/ru/helpers.md"]) {
    const text = await readFile(new URL(file, import.meta.url), "utf8");
    const found = [
      ...text.matchAll(/\|\s*`(HELPER_[A-Z_]+)`\s*\|\s*(\d+)\s*\|/g),
    ].map((m) => [m[1], Number(m[2])]);
    assert.deepEqual(found.sort(), expected.sort());
  }
});
