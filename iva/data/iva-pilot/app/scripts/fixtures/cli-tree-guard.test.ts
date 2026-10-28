/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Гвард: фикстура, поднимающая `iva` в своём дереве, берёт деревья общим списком
// (scripts/fixtures/cli-tree.ts), а не своим перечнем. Списком забывают новый пакет
// (T20: packages/secret-redaction), и CLI в фикстуре падает на импорте, которого нет.
// Правило узкое намеренно: копирование `bin/iva.mjs` или `scripts/cli` — признак того,
// что фикстура запускает CLI; сборщики (build-isolation, custom-layer) сюда не попадают.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, posix } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const HELPER = "scripts/fixtures/cli-tree.ts";
const COPY =
  /(?:cp|cpSync|copyFile)\(\s*join\(\s*(?:ROOT|root|REPO|PROJECT_ROOT)\s*,\s*"([^"]+)"/gu;

function scannedFiles(): string[] {
  const files: string[] = [];
  const visit = (relativeDirectory: string): void => {
    for (const entry of readdirSync(join(ROOT, relativeDirectory), {
      withFileTypes: true,
    })) {
      const relativePath = posix.join(relativeDirectory, entry.name);
      if (entry.isDirectory()) visit(relativePath);
      else if (
        entry.isFile() &&
        (entry.name.endsWith(".test.ts") ||
          relativeDirectory === "scripts/fixtures")
      )
        files.push(relativePath);
    }
  };
  visit("scripts");
  return files.sort();
}

test("CLI-фикстуры берут деревья общим списком", () => {
  const offenders: string[] = [];
  for (const file of scannedFiles()) {
    if (file === HELPER) continue;
    const source = readFileSync(join(ROOT, file), "utf8");
    const copies = [...source.matchAll(COPY)].map((match) => match[1]);
    // Признак CLI-фикстуры: сама копирует `bin/iva.mjs` или `scripts/cli`. Деревья
    // `packages`/`deploy` копируют и сборщики — их этот гвард не судит.
    const buildsCliTree =
      copies.includes("bin/iva.mjs") ||
      copies.some(
        (path) => path === "scripts/cli" || path.startsWith("scripts/cli/"),
      );
    if (!buildsCliTree) continue;
    offenders.push(`${file} -> ${copies.join(", ")}`);
  }
  assert.deepEqual(
    offenders,
    [],
    `фикстура собирает CLI-дерево своим перечнем, общий список — scripts/fixtures/cli-tree.ts:\n${offenders.join("\n")}`,
  );
});
