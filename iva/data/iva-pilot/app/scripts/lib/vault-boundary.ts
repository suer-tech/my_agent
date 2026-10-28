// Граница VaultDirError для процессов-скриптов: причина одной строкой в stderr и код 1,
// без стека. Резолвер каталога вольта кидает VaultDirError на пустом значении или
// пробелах по краям — точка входа обязана сказать это одной строкой (T24 v3).
import {
  VaultDirError,
  resolveVaultDir,
} from "../../packages/vault-dir/index.ts";

export function vaultDirOrExit(root: string = process.cwd()): string {
  try {
    return resolveVaultDir(root);
  } catch (error) {
    if (error instanceof VaultDirError) {
      console.error(error.message);
      process.exit(1);
    }
    throw error;
  }
}
