import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import { parseUpdateCallbackData } from "./update-callback.ts";

const SEED = 18_702;

void test("update callback parser accepts only exact actions", () => {
  assert.deepEqual(parseUpdateCallbackData("iva_update:do"), { action: "do" });
  assert.deepEqual(parseUpdateCallbackData("iva_update:skip"), {
    action: "skip",
  });
  for (const invalid of [
    "",
    "iva_update:",
    "iva_update:run",
    "iva_update:do-now",
    "iva_update:skip ",
    null,
    1,
    {},
  ]) {
    assert.equal(
      parseUpdateCallbackData(invalid),
      null,
      JSON.stringify(invalid) ?? typeof invalid,
    );
  }
});

void test("property: accepted callback data has one canonical representation", () => {
  fc.assert(
    fc.property(fc.anything(), (data) => {
      const parsed = parseUpdateCallbackData(data);
      if (parsed === null) return;
      assert.equal(typeof data, "string");
      assert.equal(data, `iva_update:${parsed.action}`);
    }),
    { seed: SEED, numRuns: 2_000 },
  );
});
