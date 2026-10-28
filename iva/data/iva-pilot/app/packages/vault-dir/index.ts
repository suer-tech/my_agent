import { resolve } from "node:path";

/**
 * Настройка каталога вольта задана противоречиво: пустое значение или пробелы по краям.
 * Молчаливая подмена каталога (дефолт или обрезанный путь) запрещена каноном, поэтому
 * это явная ошибка с именем переменной, а не фолбэк.
 */
export class VaultDirError extends Error {}

/**
 * Настройка ASSISTANT_VAULT_DIR: отсутствующая переменная — дефолт установки «vault»;
 * пустое значение и пробелы по краям — ошибка с именем переменной и подсказкой.
 */
export function vaultDirSetting(configured: string | undefined): string {
  if (configured === undefined) return "vault";
  const trimmed = configured.trim();
  if (trimmed.length === 0)
    throw new VaultDirError(
      "ASSISTANT_VAULT_DIR is empty; unset it for the default `vault` or set a path",
    );
  if (trimmed !== configured)
    throw new VaultDirError(
      `ASSISTANT_VAULT_DIR has surrounding whitespace (${JSON.stringify(configured)}); ` +
        "write the path without spaces at the ends or unset it for the default `vault`",
    );
  return configured;
}

/**
 * Канонический каталог вольта — одна формула для authored и operational процессов.
 * Относительный путь считается от базы, абсолютный берётся как задан. `configured`
 * без второго аргумента читается из ASSISTANT_VAULT_DIR процесса; явный `undefined`
 * значит «в окружении не задан», а не «возьми значение процесса».
 */
export function resolveVaultDir(root: string, configured?: string): string {
  const value =
    arguments.length > 1 ? configured : process.env.ASSISTANT_VAULT_DIR;
  return resolve(root, vaultDirSetting(value));
}
