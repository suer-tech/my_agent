// Дерево, в котором `bin/iva.mjs` может подняться: список деревьев CLI живёт здесь, а не в
// каждой фикстуре по отдельности. Списки по именам забывали: `packages/secret-redaction`
// появился, в копию не попал, и `iva` в пяти фикстурах падал на импорте, которого нет
// (слепая приёмка T20, раунд 2). Тот же приём, что у рантайма: один массив деревьев
// (RUNTIME_SOURCE_TREES в scripts/lib/custom-layer.ts) и одна функция, которая его кладёт.
import { cp, copyFile, mkdir, symlink } from "node:fs/promises";
import { join } from "node:path";

/**
 * Что нужно установке, чтобы `iva` загрузился без authored tree (ADR-0003): сам CLI, его
 * библиотека, локальные пакеты (`#`-алиасы сюда не смотрят, импорты относительные) и
 * `deploy/` — оттуда читаются юниты. `agent/` в списке нет намеренно: CLI обязан работать
 * и без него (scripts/authored-tree-guard.test.ts).
 */
export const CLI_FIXTURE_TREES = [
  "scripts/cli",
  "scripts/lib",
  "packages",
  "deploy",
] as const;

/**
 * Поднять дерево CLI внутри `project`. Деревья из `copy` копируются (их тест правит у
 * себя), остальные приходят ссылкой на репозиторий — так фикстура остаётся дешёвой.
 */
export async function plantCliTree(
  root: string,
  project: string,
  { copy = [] as readonly string[] } = {},
): Promise<void> {
  await mkdir(join(project, "bin"), { recursive: true });
  await mkdir(join(project, "scripts"), { recursive: true });
  await copyFile(join(root, "bin/iva.mjs"), join(project, "bin/iva.mjs"));
  for (const tree of CLI_FIXTURE_TREES) {
    if (copy.includes(tree))
      await cp(join(root, tree), join(project, tree), { recursive: true });
    else await symlink(join(root, tree), join(project, tree), "dir");
  }
}
