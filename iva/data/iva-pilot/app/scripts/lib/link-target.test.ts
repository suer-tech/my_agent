/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns test registration. */
// throughLink: симлинк → цель одним переходом, обычный и отсутствующий путь — сами.
// Фикстура во временном каталоге под .scratch/ дерева (не /tmp): симлинки — это файлы,
// и жить им рядом с кодом, а не в системном temp.
import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { throughLink } from "./link-target.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function scratch(t: TestContext): string {
  const base = join(ROOT, ".scratch", "b4b-link-target");
  mkdirSync(base, { recursive: true });
  const dir = mkdtempSync(join(base, "case-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("симлинк ведёт к цели одним переходом", (t: TestContext) => {
  const dir = scratch(t);
  const target = join(dir, "target.txt");
  writeFileSync(target, "x");
  const link = join(dir, "link.txt");
  symlinkSync(target, link);

  assert.equal(throughLink(link), target);
});

test("обычный путь возвращается как есть", (t: TestContext) => {
  const dir = scratch(t);
  const plain = join(dir, "plain.txt");
  writeFileSync(plain, "x");

  assert.equal(throughLink(plain), plain);
});

test("отсутствующий путь возвращается как есть", (t: TestContext) => {
  const dir = scratch(t);

  assert.equal(throughLink(join(dir, "missing.txt")), join(dir, "missing.txt"));
});
