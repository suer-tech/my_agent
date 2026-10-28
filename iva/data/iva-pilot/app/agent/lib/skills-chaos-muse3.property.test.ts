// PBT-MUSE-3: custom skills под криворуким пользователем. Читаем только
// readCustomSkills (случайные деревья уже держит соседний property-тест) —
// здесь размеры, симлинки и инварианты описаний.
//
// КАК ВОСПРОИЗВЕСТИ: при провале fast-check печатает
// `Property failed ... { seed: N, path: "...", endOnFailure: true }` —
// подставь вторым аргументом fc.assert(prop, { seed: N, path: "..." }).
// Детерминированные тесты воспроизводятся сами.
import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import fc from "fast-check";
import { readCustomSkills } from "./custom-skills.ts";

const SEED = 20260301;

const worlds: string[] = [];

function world(): string {
  const dir = mkdtempSync(join(tmpdir(), "iva-skills-muse3-"));
  worlds.push(dir);
  return dir;
}

// Чистим в finally каждого теста, а не в after(): раннер при
// --test-name-pattern стреляет file-level after() до завершения живого теста
// и сносит его каталог (F2 ловил ENOENT вместо висяка).
function cleanup(): void {
  for (const dir of worlds.splice(0))
    rmSync(dir, { recursive: true, force: true });
}

function write(root: string, path: string, contents: string | Buffer): void {
  const target = join(root, path);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, contents);
}

const logs: string[] = [];
const read = (dir: string) =>
  readCustomSkills(dir, (line) => {
    logs.push(line);
  });

// F1: тело скилла и соседние файлы пакета режутся потолком размера —
// гигант пропускается с строкой в журнал, а не едет в контекст каждого хода.
await test("F1: гигантский SKILL.md пропускается, гигантский сосед отбрасывается", async () => {
  try {
    const dir = world();
    logs.length = 0;
    const body = `---\ndescription: big\n---\n# Big\n${"x".repeat(8 * 1024 * 1024)}\n`;
    write(dir, "big.md", body);
    write(dir, "pack/SKILL.md", "---\ndescription: pack\n---\n# Pack\n");
    const neighbor = Buffer.alloc(5 * 1024 * 1024, 7);
    write(dir, "pack/references/dump.bin", neighbor);
    const skills = await read(dir);
    assert.ok(!("big" in skills), "тело-гигант пропускается целиком");
    assert.ok(
      logs.some((line) => line.includes("big.md")),
      "пропуск тела виден в журнале",
    );
    assert.ok(skills["pack"], "пакет с отброшенным соседом остаётся");
    assert.ok(
      !("references/dump.bin" in (skills["pack"].files ?? {})),
      "сосед-гигант отбрасывается",
    );
    assert.ok(
      logs.some((line) => line.includes("dump.bin")),
      "пропуск соседа виден в журнале",
    );
  } finally {
    cleanup();
  }
});

// F2: читать только обычные файлы — блочные/символьные устройства и FIFO
// пропускаются до чтения, ход не виснет. Прямой вызов: после починки резолвится
// за миллисекунды (до починки этот тест вешал раннер — красный показывали
// дочерним процессом с SIGTERM).
await test("F2: симлинк скилла на /dev/zero пропускается, чтение завершается", async () => {
  try {
    const dir = world();
    logs.length = 0;
    symlinkSync("/dev/zero", join(dir, "zero.md"));
    const skills = await read(dir);
    assert.ok(!("zero" in skills), "устройство не становится скиллом");
    assert.ok(
      logs.some((line) => line.includes("zero.md")),
      "пропуск виден в журнале",
    );
  } finally {
    cleanup();
  }
});

// Инварианты describe() через публичный вход: произвольное тело всегда даёт
// описание не длиннее 121 символа, без одинокого суррогата на срезе, без бросков.
await test("property: описание из любого тела короткое и без битых суррогатов", async () => {
  try {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ maxLength: 2000 }),
        fc.string({ maxLength: 300 }),
        async (body, declared) => {
          const dir = world();
          const markdown = `---\ndescription: ${JSON.stringify(declared).slice(1, -1)}\n---\n${body}`;
          write(dir, "s.md", markdown);
          const skills = await read(dir);
          const description = skills["s"].description;
          assert.ok(description.length <= 121, `длина ${description.length}`);
          assert.ok(
            !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/u.test(description),
            "нет одинокого старшего суррогата",
          );
          assert.ok(
            !/(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(description),
            "нет одинокого младшего суррогата",
          );
        },
      ),
      { seed: SEED + 1, numRuns: 100 },
    );
  } finally {
    cleanup();
  }
});

// Мусор в именах записей никогда не роняет чтение: либо скилл, либо пропуск с логом.
await test("property: мусорные имена записей — скилл или пропуск, но не бросок", async () => {
  try {
    await fc.assert(
      fc.asyncProperty(
        fc.array(
          fc
            .string({ minLength: 1, maxLength: 24 })
            .map((s) => s.replace(/\//gu, "_")),
          { maxLength: 12 },
        ),
        async (names) => {
          logs.length = 0;
          const dir = world();
          for (const [index, name] of names.entries())
            write(
              dir,
              `${index}-${name}`,
              `---\ndescription: d${index}\n---\n# T\n`,
            );
          const skills = await read(dir);
          for (const name of Object.keys(skills))
            assert.match(name, /^[A-Za-z0-9][A-Za-z0-9._-]*$/);
        },
      ),
      { seed: SEED + 2, numRuns: 100 },
    );
  } finally {
    cleanup();
  }
});
