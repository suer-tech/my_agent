// Шумовые свойства ночного .gitignore вольта: файл владельца можно только ДОПИСЫВАТЬ.
// Контракт `ensureVaultGitignore` («Только ДОПИСЫВАЕТ: чужой .gitignore не
// перезаписывается и ни одна его строка не удаляется») проверяется на нечитаемом файле:
// readFileSync падает не только ENOENT, а код трактует любой сбой как «файла нет» и
// создаёт новый — поверх владельческого.
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: при провале fast-check печатает строку вида
// `Property failed after N tests { seed: -1234567, path: "12:3:0", endOnFailure: true }`.
// Подставь её вторым аргументом — fc.assert(prop, { seed: -1234567, path: "12:3:0" }) —
// и прогон повторится байт в байт, включая shrink.
import test from "node:test";
import assert from "node:assert/strict";
import {
  chmodSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import fc from "fast-check";
import { ensureVaultGitignore } from "./memory-maintenance.ts";

function world(content: string | null) {
  const dir = mkdtempSync(join(tmpdir(), "iva-gitignore-pbt-"));
  const file = join(dir, ".gitignore");
  if (content !== null) writeFileSync(file, content);
  return { dir, file };
}

await test("нечитаемый .gitignore не затирается: строки владельца остаются на месте", () => {
  fc.assert(
    fc.property(fc.string({ minLength: 1, maxLength: 200 }), (raw) => {
      const content = raw.endsWith("\n") ? raw : `${raw}\n`;
      const { dir, file } = world(content);
      let after: string;
      try {
        chmodSync(file, 0o000);
        try {
          ensureVaultGitignore(dir);
        } finally {
          chmodSync(file, 0o600);
        }
        after = readFileSync(file, "utf8");
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
      assert.equal(
        after,
        content,
        "нечитаемый .gitignore был перезаписан: правила владельца потеряны",
      );
    }),
    { seed: 20_260_918, numRuns: 1 },
  );
});

await test("читаемый .gitignore дозаписывается, а не переписывается", () => {
  const content = "# owner rules\n*.secret\nvault-private/\n";
  const { dir, file } = world(content);
  try {
    assert.equal(
      ensureVaultGitignore(dir),
      true,
      "паттерны должны быть дозалиты",
    );
    const after = readFileSync(file, "utf8");
    assert.ok(after.startsWith(content), "исходные строки обязаны уцелеть");
    assert.match(after, /^\*\.tmp$/mu);
    assert.match(after, /^\*\.tmp-\*$/mu);
    assert.equal(
      ensureVaultGitignore(dir),
      false,
      "повторный вызов ничего не делает",
    );
    assert.equal(readFileSync(file, "utf8"), after);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

await test("отсутствующий .gitignore создаётся с шаблонами", () => {
  const { dir, file } = world(null);
  try {
    assert.equal(ensureVaultGitignore(dir), true);
    assert.equal(
      readFileSync(file, "utf8"),
      "# unfinished atomic writes (temp files of an interrupted writer)\n*.tmp\n*.tmp-*\n",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
