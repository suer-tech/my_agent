import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

export function eveReleaseVersion(root: string): string {
  const match = readFileSync(
    join(root, "scripts/eve-release.sh"),
    "utf8",
  ).match(/^version="([^"]+)"$/m);
  assert.ok(match, "eve-release.sh must declare its archive version");
  return match[1];
}
