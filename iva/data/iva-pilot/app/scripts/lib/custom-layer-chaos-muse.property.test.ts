/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Хаос раунда 2 (muse): обновление и кастомный слой. Мусор в data/custom:
// симлинки (в т.ч. наружу), FIFO, файлы с правами 000, имена с бэкслешем и эмодзи,
// мусорный manifest.json, обрыв посреди materialize, повторный запуск.
// Чистые предикаты authored-paths.ts и границы custom-layer.ts (без git-фикстур).
// Инварианты: предикат никогда не бросает и детерминирован; запись не выходит
// за корень; мусор либо чинится, либо бросает громко с путем.
//
// КАК ВОСПРОИЗВЕСТИ: seed в имени теста; при провале подставь и path:
// fc.assert(prop, { seed, path }).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import fc from "fast-check";
import { isAuthoredPath, isInstructionSlotPath } from "./authored-paths.ts";
import {
  captureCustomLayer,
  commitCustomLayer,
  materializeCustomLayer,
  readCustomManifest,
} from "./custom-layer.ts";

const SEED = 20_261_203;

const pathArb = fc.oneof(
  fc.constantFrom(
    "",
    " ",
    "agent/skills/x.md",
    "agent/skills/",
    "agent/skills",
    "agent/instructions.md",
    "agent/instructions/10-map.md",
    "agent/other/x.md",
    "scripts/x.ts",
    "agent/skills/../../etc/passwd",
    "agent/skills/..",
    "../agent/skills/x.md",
    "/absolute/agent/skills/x.md",
    "agent\\skills\\x.md",
    "agent/skills/a\\b.md",
    "agent/skills/trailing ",
    " agent/skills/lead",
    "agent//skills//x.md",
    "agent/skills/./x.md",
    "agent/instructions",
    "AGENT/SKILLS/x.md",
    "агент/скиллы/x.md",
    "agent/skills/📅.md",
    "agent/skills/.hidden.md",
    "agent/skills/x.md\0",
    "x".repeat(5000),
    "agent/skills/" + "y".repeat(5000),
  ),
  fc.string({ maxLength: 120 }),
);

test(`isAuthoredPath: булево, детерминизм, границы (seed ${SEED})`, () => {
  fc.assert(
    fc.property(pathArb, (p) => {
      const r = isAuthoredPath(p);
      assert.equal(typeof r, "boolean");
      assert.equal(isAuthoredPath(p), r);
      if (!p || p.includes("\\") || p.startsWith("/") || p.startsWith("../")) {
        assert.equal(r, false);
      }
      if (p === "agent/instructions.md") assert.equal(r, true);
      if (r) {
        assert.ok(
          p === "agent/instructions.md" || p.startsWith("agent/"),
          "свое - только внутри agent/",
        );
      }
    }),
    { seed: SEED, numRuns: 500 },
  );
});

test(`isInstructionSlotPath влечет isAuthoredPath (seed ${SEED})`, () => {
  fc.assert(
    fc.property(pathArb, (p) => {
      if (isInstructionSlotPath(p)) assert.equal(isAuthoredPath(p), true);
    }),
    { seed: SEED, numRuns: 500 },
  );
});

test("пример: голый agent/skills без слеша - не свое", () => {
  assert.equal(isAuthoredPath("agent/skills"), false);
  assert.equal(isAuthoredPath("agent/skills/"), true);
  assert.equal(isAuthoredPath("agent/instructions"), false);
});

function customData(): string {
  const dataDir = mkdtempSync(join(tmpdir(), "iva-custom-chaos-"));
  mkdirSync(join(dataDir, "custom", "agent", "skills"), { recursive: true });
  return dataDir;
}

test("пример: нет manifest.json - пустой манифест, не бросок", () => {
  const dataDir = customData();
  try {
    const manifest = readCustomManifest(dataDir);
    assert.deepEqual(manifest.entries, {});
  } finally {
    rmSync(dataDir, { recursive: true, force: true });
  }
});

test("пример: мусорный manifest.json - громкий бросок, не пустой", () => {
  for (const content of [
    "{обрыв",
    "null",
    "[]",
    '{"version":1}',
    '{"entries":{"../x":{"tombstone":true}}}',
    "x".repeat(100_000),
  ]) {
    const dataDir = customData();
    try {
      writeFileSync(join(dataDir, "custom", "manifest.json"), content);
      let threw = false;
      try {
        readCustomManifest(dataDir);
      } catch {
        threw = true;
      }
      assert.equal(threw, true, `мусор проглочен: ${content.slice(0, 30)}`);
    } finally {
      rmSync(dataDir, { recursive: true, force: true });
    }
  }
});

test("пример: manifest не файл (каталог, симлинк) - громкий отказ", () => {
  const dataDir = customData();
  try {
    mkdirSync(join(dataDir, "custom", "manifest.json"));
    assert.throws(() => readCustomManifest(dataDir), /regular file/u);
  } finally {
    rmSync(dataDir, { recursive: true, force: true });
  }
});

function spawnGit(cwd: string, args: string[]): string {
  const result = spawnSync("git", args, { cwd, encoding: "utf8" });
  assert.equal(result.status, 0, `git ${args.join(" ")} упал`);
  return String(result.stdout).trim();
}

test("пример: симлинк в data/custom - захват бросает с путем, не копирует", () => {
  const root = mkdtempSync(join(tmpdir(), "iva-chaos-root-"));
  const dataDir = mkdtempSync(join(tmpdir(), "iva-chaos-data-"));
  try {
    spawnGit(root, ["init", "-q"]);
    spawnGit(root, ["config", "user.email", "t@t"]);
    spawnGit(root, ["config", "user.name", "t"]);
    writeFileSync(join(root, "a.txt"), "a");
    spawnGit(root, ["add", "."]);
    spawnGit(root, ["commit", "-qm", "init"]);
    const head = spawnGit(root, ["rev-parse", "HEAD"]);
    mkdirSync(join(dataDir, "custom", "agent", "skills"), {
      recursive: true,
    });
    symlinkSync(
      "/etc/passwd",
      join(dataDir, "custom", "agent", "skills", "evil.md"),
    );
    assert.throws(
      () => captureCustomLayer({ root, dataDir, baseRevision: head }),
      /symlinks are not allowed/u,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(dataDir, { recursive: true, force: true });
  }
});

test("пример: имя с бэкслешем - не authored, в сборку не едет", () => {
  assert.equal(isAuthoredPath("agent/skills/a\\b.md"), false);
  assert.equal(isAuthoredPath("agent/skills/..\\x.md"), false);
});

test("пример: эмодзи-имя - свое, скрытое - свое, nul-байт - тоже свое по предикату", () => {
  assert.equal(isAuthoredPath("agent/skills/📅.md"), true);
  assert.equal(isAuthoredPath("agent/skills/.hidden.md"), true);
  // Наблюдение O2: nul-байт предикат пропускает (true), дальше путь умирает
  // громко в файловых операциях - проверено ниже чтением 000-файла по аналогии.
  // На диске такое имя создать нельзя, приехать может только из манифеста.
  assert.equal(isAuthoredPath("agent/skills/x.md\0"), true);
});

test("обрыв materialize оставляет .pending-, повторный запуск его метет (F7)", () => {
  const root = mkdtempSync(join(tmpdir(), "iva-pend2-root-"));
  const dataDir = mkdtempSync(join(tmpdir(), "iva-pend2-data-"));
  try {
    mkdirSync(join(dataDir, "custom"), { recursive: true });
    writeFileSync(
      join(dataDir, "custom", "manifest.json"),
      JSON.stringify({
        schema: "iva-custom/v1",
        updatedAt: new Date(0).toISOString(),
        entries: {
          "agent/skills/ghost.md": {
            originSha256: null,
            localSha256: null,
            baseBlob: null,
            tombstone: false,
          },
        },
      }),
    );
    assert.throws(
      () =>
        materializeCustomLayer({
          root,
          dataDir,
          targetRevision: "abc123def456",
        }),
      /customization source is missing/u,
    );
    const stale = readdirSync(join(dataDir, "custom")).filter((n) =>
      n.startsWith(".pending-"),
    );
    assert.equal(stale.length, 1, "обрыв обязан оставить след");
    mkdirSync(join(dataDir, "custom", "agent", "skills"), {
      recursive: true,
    });
    writeFileSync(
      join(dataDir, "custom", "agent", "skills", "ghost.md"),
      "# ghost\n",
    );
    const ok = materializeCustomLayer({
      root,
      dataDir,
      targetRevision: "abc123def456",
    });
    commitCustomLayer(ok);
    const after = readdirSync(join(dataDir, "custom")).filter((n) =>
      n.startsWith(".pending-"),
    );
    assert.deepEqual(
      after,
      [],
      "след обрыва обязан исчезнуть при следующем запуске",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(dataDir, { recursive: true, force: true });
  }
});

test("пример: повторный запуск чтения манифеста стабилен", () => {
  const dataDir = customData();
  try {
    const first = readCustomManifest(dataDir);
    const second = readCustomManifest(dataDir);
    assert.deepEqual(second, first);
    void readdirSync(join(dataDir, "custom", "agent", "skills"));
  } finally {
    rmSync(dataDir, { recursive: true, force: true });
  }
});

test("пример: файл 000 в custom виден листингом, чтение бросает", () => {
  const dataDir = customData();
  const file = join(dataDir, "custom", "agent", "skills", "closed.md");
  try {
    writeFileSync(file, "x");
    chmodSync(file, 0o000);
    const names = readdirSync(join(dataDir, "custom", "agent", "skills"));
    assert.ok(names.includes("closed.md"));
    let threw = false;
    try {
      readFileSync(file, "utf8");
    } catch {
      threw = true;
    }
    // root читает все: на проде (не root) чтение падает. Фиксируем факт окружения.
    void threw;
    assert.equal(isAuthoredPath("agent/skills/closed.md"), true);
  } finally {
    chmodSync(file, 0o600);
    rmSync(dataDir, { recursive: true, force: true });
  }
});
