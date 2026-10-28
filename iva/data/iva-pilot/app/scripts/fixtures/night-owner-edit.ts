// Deterministic direct owner save in a night writer's filesystem window.
import fs from "node:fs";
import { execFileSync } from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { mock } from "node:test";

Error.stackTraceLimit = 40;
const file = process.env.IVA_TEST_OWNER_FILE;
const trigger = process.env.IVA_TEST_OWNER_TRIGGER;
let edited = false;
function ownerEdit(stack: string, path: string) {
  if (!file || !trigger || edited || !stack.includes(trigger)) return;
  if (process.env.IVA_TEST_OWNER_STEP === "read" && path !== file) return;
  if (
    process.env.IVA_TEST_OWNER_STEP === "card" &&
    fs.realpathSync(path) !== fs.realpathSync(file)
  )
    return;
  if (
    process.env.IVA_TEST_OWNER_STEP === "cache" &&
    !path.includes("memory/night/")
  )
    return;
  edited = true;
  execFileSync(process.execPath, [
    "--input-type=module",
    "--eval",
    `
    import { readFileSync, writeFileSync, existsSync } from "node:fs";
    const file = ${JSON.stringify(file)};
    const before = existsSync(file) ? readFileSync(file, "utf8") : "";
    writeFileSync(file, before.replace("Первая строка правды", "Правда владельца") + "\\nOWNER_EDIT\\n");
  `,
  ]);
  console.error(`OWNER_EDIT_WINDOW ${trigger}`);
}
const read = fs.readFileSync;
const rename = fs.renameSync;
mock.method(
  fs,
  "readFileSync",
  (...args: Parameters<typeof fs.readFileSync>) => {
    const value = read(...args);
    if (process.env.IVA_TEST_OWNER_STEP === "read")
      ownerEdit(new Error().stack ?? "", String(args[0]));
    return value;
  },
);
mock.method(fs, "renameSync", (...args: Parameters<typeof fs.renameSync>) => {
  rename(...args);
  if (process.env.IVA_TEST_OWNER_STEP !== "read")
    ownerEdit(new Error().stack ?? "", String(args[1]));
});
syncBuiltinESMExports();
