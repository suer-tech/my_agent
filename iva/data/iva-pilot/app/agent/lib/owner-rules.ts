// Правила владельца — markdown-файлы в data/custom/agent/instructions/, рядом со встроенной
// персоной. Их читает с диска agent/instructions/30-owner-rules.ts на каждый ход, пишет
// write_file (прочитать файл, дописать строку, записать целиком), а сборка в дерево
// не копирует (scripts/lib/authored-paths.ts: isLiveInstructionPath). Правила always-on
// и платятся каждым ходом, отсюда общий предел: доктор предупреждает о переборе,
// источник не режет текст молча.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { dataDir } from "./data-dir.ts";

export const OWNER_RULES_CAP = 4000;

export type OwnerRulesFile = { readonly name: string; readonly body: string };

export function ownerRulesDir(): string {
  return join(dataDir(), "custom", "agent", "instructions");
}

function absent(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | null)?.code;
  return code === "ENOENT" || code === "ENOTDIR";
}

/** Файлы слота по именам, как их сортирует eve; нет каталога — пусто. */
export function readOwnerRules(dir: string): {
  readonly files: readonly OwnerRulesFile[];
  readonly chars: number;
} {
  let names: string[];
  try {
    names = readdirSync(dir, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.endsWith(".md"))
      .map((entry) => entry.name)
      .sort((a, b) => a.localeCompare(b));
  } catch (error) {
    if (absent(error)) return { files: [], chars: 0 };
    throw error;
  }
  const files: OwnerRulesFile[] = [];
  let chars = 0;
  for (const name of names) {
    let body: string;
    try {
      body = readFileSync(join(dir, name), "utf8");
    } catch (error) {
      if (absent(error)) continue; // файл удалён между листингом и чтением
      throw error;
    }
    files.push({ name, body });
    chars += body.length;
  }
  return { files, chars };
}

/** Текст для промпта: подзаголовок с именем файла перед каждым; пусто — пустая строка. */
export function ownerRulesMarkdown(dir: string): string {
  const parts = readOwnerRules(dir)
    .files.map(({ name, body }) => ({ name, body: body.trim() }))
    .filter(({ body }) => body.length > 0)
    .map(({ name, body }) => `### ${name}\n${body}`);
  if (parts.length === 0) return "";
  return `## Owner rules (data/custom/agent/instructions/)\n${parts.join("\n\n")}`;
}
