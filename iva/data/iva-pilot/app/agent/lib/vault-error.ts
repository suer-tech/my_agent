// Границы VaultDirError в authored tree: резолвер каталога вольта отказывает явно
// (пустое значение, пробелы по краям), и этот отказ обязан доехать до потребителя
// текстом, а не стеком на уровне импорта или необработанным исключением в ходе.
import { VaultDirError } from "@iva/vault-dir";

/** Текст отказа резолвера или null: чужие ошибки границы не подменяют. */
export function vaultDirErrorText(error: unknown): string | null {
  return error instanceof VaultDirError ? error.message : null;
}

/** Блок динамической инструкции: владелец видит причину прямо в контексте хода. */
export function vaultMisconfiguredMarkdown(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return [
    "## Хранилище не настроено",
    `vault is misconfigured: ${text}`,
    "Почини ASSISTANT_VAULT_DIR в .env (пустое значение недопустимо) и перезапусти: `iva restart`.",
  ].join("\n");
}
