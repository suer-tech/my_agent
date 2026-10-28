// Хаос-прогон командной строки: мусор в имени команды и в аргументах. Найдено 2026-09-13
// маршрутом pbt/iva-deepseek-4, раунд 2.
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: seed стоит в имени свойства; при провале fast-check печатает
// `{ seed, path, endOnFailure: true }`, который повторяет прогон байт в байт.
//
// КРАСНЫЙ тест здесь - находка; починка описана в отчёте
// `.scratch/work/reviews/pbt-iva-deepseek-4-2026-09-12.md` (раздел «Раунд 2»).

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";
import fc from "fast-check";
import { createCliMain, dispatchCli, type CliCommand } from "./main.ts";
import { parsePostArguments } from "./post.ts";

const SEED = 20_260_913;
const ROOT = join(import.meta.dirname, "../..");
const ENTRY = join(ROOT, "bin/iva.mjs");

class ExitSignal extends Error {}

function recorder() {
  const events: string[] = [];
  return {
    events,
    bad: (message: string) => events.push(`bad:${message}`),
    help: () => events.push("help"),
    exit: (code: number): never => {
      events.push(`exit:${code}`);
      throw new ExitSignal(`exit:${code}`);
    },
  };
}

async function dispatchOutcome(
  commands: Readonly<Record<string, CliCommand>>,
  argv: readonly string[],
): Promise<string[]> {
  const { events, bad, help, exit } = recorder();
  try {
    const result = await dispatchCli(argv, commands, { bad, help, exit });
    events.push(`resolved:${typeof result}`);
  } catch (error) {
    if (!(error instanceof ExitSignal)) events.push(`threw:${String(error)}`);
  }
  return events;
}

// НАХОДКА R2-4. Имя команды ищется как свойство обычного объекта, поэтому имена из
// цепочки прототипов считаются существующими командами. `iva constructor` вызывает
// Object(rest) и молча завершается с кодом 0, будто команда сработала; `iva toString`,
// `iva __proto__` и `iva valueOf` печатают внутреннюю ошибку движка ("Cannot convert
// undefined or null to object") вместо «Unknown command». Пользователь опечатался или
// вставил имя объекта - и не узнал, что команды нет.
await test("НАХОДКА R2-4: имя из прототипа - неизвестная команда, а не молчаливый успех", async () => {
  const { commands } = createCliMain(join(tmpdir(), "iva-cli-chaos-"));
  for (const name of [
    "constructor",
    "toString",
    "valueOf",
    "hasOwnProperty",
    "__proto__",
    "isPrototypeOf",
    "propertyIsEnumerable",
  ]) {
    assert.equal(Object.hasOwn(commands, name), false, name);
    assert.deepEqual(
      await dispatchOutcome(commands, [name]),
      [`bad:Unknown command: ${name}`, "help", "exit:1"],
      `имя ${name}`,
    );
  }
});

await test(`НАХОДКА R2-4: свойство «чужое имя всегда неизвестно» (seed ${SEED})`, async () => {
  const { commands } = createCliMain(join(tmpdir(), "iva-cli-chaos-"));
  // Пустая строка - это «команда не названа»: help и код 0, как у голого `iva`.
  const namePool = [
    "constructor",
    "toString",
    "valueOf",
    "__proto__",
    "hasOwnProperty",
    "unknown",
    "iva",
    "help!",
    "--",
    "更新",
    "\u0000",
  ];
  await fc.assert(
    fc.asyncProperty(fc.constantFrom(...namePool), async (name) => {
      if (Object.hasOwn(commands, name)) return;
      const events = await dispatchOutcome(commands, [name]);
      assert.deepEqual(events, [
        `bad:Unknown command: ${name}`,
        "help",
        "exit:1",
      ]);
    }),
    { seed: SEED, numRuns: 60 },
  );
});

// Зелёный control: парсер аргументов поста держит свои границы на случайном мусоре -
// неизвестный флаг и флаг без значения всегда отказ, и успех возможен только при ровно
// одном источнике текста.
await test("зелёное: неизвестный флаг и флаг без значения всегда отказ", () => {
  const known = [
    "--md",
    "--md-file",
    "--chat",
    "--thread-id",
    "--silent",
    "--allow-upload",
    "--dry-run",
    "value",
  ] as const;
  fc.assert(
    fc.property(
      fc.array(fc.constantFrom(...known), { maxLength: 8 }),
      (argv) => {
        let parsed: ReturnType<typeof parsePostArguments>;
        try {
          parsed = parsePostArguments(argv);
        } catch (error) {
          assert.match(String((error as Error).message), /--/u);
          return;
        }
        // Успех возможен только когда задан ровно один источник текста.
        assert.notEqual(parsed.md === undefined, parsed.mdFile === undefined);
      },
    ),
    { seed: SEED, numRuns: 300 },
  );

  fc.assert(
    fc.property(
      fc.constantFrom("--bogus", "--token", "-x", "text", ""),
      fc.array(fc.constantFrom("--md", "--md-file", "value", "--silent"), {
        maxLength: 5,
      }),
      (unknown, rest) => {
        assert.throws(
          () => parsePostArguments([unknown, ...rest]),
          /Unknown option/u,
        );
      },
    ),
    { seed: SEED, numRuns: 100 },
  );

  assert.throws(() => parsePostArguments(["--md"]), /needs a value/u);
  assert.throws(
    () => parsePostArguments(["--md", "a", "--md-file", "b"]),
    /exactly one/u,
  );
});

await test("зелёное: мусорные аргументы не роняют процесс и не печатают стек", (t: TestContext) => {
  const home = mkdtempSync(join(tmpdir(), "iva-cli-chaos-home-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const vectors = [
    [""],
    ["--"],
    ["unknown"],
    ["help", "x".repeat(100_000)],
    ["\ud800"],
    ["更新", "更新"],
    ["constructor"],
  ];
  for (const args of vectors) {
    const result = spawnSync(process.execPath, [ENTRY, ...args], {
      cwd: ROOT,
      encoding: "utf8",
      timeout: 30_000,
      env: {
        ...process.env,
        HOME: home,
        ASSISTANT_DATA_DIR: join(home, "data"),
        NO_COLOR: "1",
      },
    });
    assert.equal(result.signal, null, `args=${JSON.stringify(args)} убит`);
    assert.ok(
      result.status === 0 || result.status === 1,
      `args=${JSON.stringify(args)} exit=${String(result.status)}`,
    );
    const output = `${result.stdout}${result.stderr}`;
    assert.doesNotMatch(
      output,
      /(TypeError|ReferenceError|ERR_[A-Z_]+)/u,
      `args=${JSON.stringify(args)}`,
    );
    assert.doesNotMatch(output, /^\s+at\s.*node:internal/mu);
  }
});
