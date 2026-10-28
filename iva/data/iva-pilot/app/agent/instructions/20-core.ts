import { defineDynamic, defineInstructions } from "eve/instructions";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { CORE_CAP } from "../lib/core-cap.ts";
import { clampCore } from "../lib/core-clamp.ts";
import {
  vaultDirErrorText,
  vaultMisconfiguredMarkdown,
} from "../lib/vault-error.ts";
import { resolveVaultDir } from "@iva/vault-dir";

// Динамическая инструкция: каждый турн инжектит CORE (vault/CORE.md) в системный
// промпт — кто пользователь, постоянные предпочтения, активные цели, указатели. Это always-on
// RAM памяти (аналог core memory у MemGPT): маленькое, переживает компактацию (инструкции —
// не часть сжимаемой истории диалога). Пишет CORE ночной rollup; живой чат правит его только
// на явное «запомни …». Clamp чистый и общий с ночным brain.
function coreMarkdown(): string {
  let core: string;
  try {
    core = readFileSync(
      join(resolveVaultDir(process.cwd()), "CORE.md"),
      "utf8",
    ).trim();
  } catch (error) {
    // Неверная настройка — блок с причиной владельцу; отсутствие файла — тишина.
    if (vaultDirErrorText(error) !== null)
      return vaultMisconfiguredMarkdown(error);
    return ""; // нет файла (vault не инициализирован) — молча ничего не инжектим.
  }
  if (!core) return "";
  // Тот же section-aware backstop, что у brain: указатели нельзя отрезать слепым slice.
  if (core.length > CORE_CAP) core = clampCore(core);
  return `## CORE — кто пользователь и что в работе\n${core}`;
}

export default defineDynamic({
  events: {
    // turn.started — перечитывается каждый турн, чтобы CORE не «застывал» после правок.
    "turn.started": () => defineInstructions({ markdown: coreMarkdown() }),
  },
});
