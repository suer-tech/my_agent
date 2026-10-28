/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// T24 v3: границы VaultDirError по классам — инструкция отдаёт блок с причиной, экран
// памяти показывает причину, хелпер процессов не бросает на корректном значении.
import assert from "node:assert/strict";
import { test } from "node:test";
import { vaultDirOrExit } from "./lib/vault-boundary.ts";
import { vaultMisconfiguredMarkdown } from "../agent/lib/vault-error.ts";

async function withEmptyVault<T>(fn: () => Promise<T>): Promise<T> {
  const previous = process.env.ASSISTANT_VAULT_DIR;
  process.env.ASSISTANT_VAULT_DIR = "";
  try {
    return await fn();
  } finally {
    if (previous === undefined) delete process.env.ASSISTANT_VAULT_DIR;
    else process.env.ASSISTANT_VAULT_DIR = previous;
  }
}

test("инструкции CORE и PERSONA отдают блок с причиной", async () => {
  await withEmptyVault(async () => {
    for (const file of [
      "../agent/instructions/20-core.ts",
      "../agent/instructions/25-persona.ts",
    ]) {
      const module = (await import(file)) as {
        default: { events: Record<string, () => Promise<unknown>> };
      };
      const handler = module.default.events["turn.started"];
      const result = (await handler()) as { markdown?: string };
      assert.match(
        String(result.markdown ?? ""),
        /vault is misconfigured: .*ASSISTANT_VAULT_DIR/,
        `${file}: блока с причиной нет`,
      );
    }
  });
});

test("экран памяти показывает причину, а не «ядро пусто»", async () => {
  await withEmptyVault(async () => {
    const screen = (await import("../scripts/lib/menu/core.ts")).default;
    const context = {
      tr: (_en: string, ru: string) => ru,
      btn: (text: string, data: string) => ({ text, callback_data: data }),
      getLang: () => "ru",
      backRow: () => [],
      deps: {},
      flows: {},
    };
    const view = await screen.render({} as never, context as never);
    assert.match(view.text, /Хранилище не настроено/);
    assert.match(view.text, /ASSISTANT_VAULT_DIR/);
    assert.ok(!view.text.includes("Ядро памяти пусто"));
  });
});

test("блок причины называет переменную и подсказку", () => {
  const block = vaultMisconfiguredMarkdown(
    new Error("ASSISTANT_VAULT_DIR is empty; unset it for the default `vault`"),
  );
  assert.match(block, /vault is misconfigured:/);
  assert.match(block, /ASSISTANT_VAULT_DIR is empty/);
});

test("vaultDirOrExit на корректном значении отдаёт каталог", () => {
  assert.equal(vaultDirOrExit("/srv/iva"), "/srv/iva/vault");
});
