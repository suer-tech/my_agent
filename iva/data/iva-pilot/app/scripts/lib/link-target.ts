// Куда ведёт ссылка — один переход, без полного резолва. Отдельный модуль, а не
// `version-layout.ts`: тот тянет `version-store.ts` (а тот — `env-file.ts`), и обратный
// импорт замыкал цикл env-file → version-layout → version-store → env-file (B4b).
import { lstatSync, readlinkSync } from "node:fs";
import { dirname, resolve } from "node:path";

/**
 * What a symlink names, target or no target. One hop, not a full resolve: writing
 * *through* the link is what keeps a version from turning shared state into its own.
 */
export function throughLink(path: string): string {
  try {
    return lstatSync(path).isSymbolicLink()
      ? resolve(dirname(path), readlinkSync(path))
      : path;
  } catch {
    return path;
  }
}
