import { createHash } from "node:crypto";
import { lstat, readFile, readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import path from "node:path";
export type EveTreeKind = "packed" | "source";
const OUTPUT_DIRS = new Set(
  ["", "packages/eve/", "packages/eve-catalog/"].flatMap((prefix) =>
    ["node_modules", "dist", ".generated", ".turbo"].map((dir) => prefix + dir),
  ),
);
function excluded(relativePath: string, kind: EveTreeKind): boolean {
  if (kind === "packed") {
    return (
      relativePath === "BUILD.json" ||
      relativePath === "dist/src/compiled/.vendor-stamp.json"
    );
  }
  return (
    relativePath === "packages/eve/BUILD.json" ||
    relativePath === "packages/eve/LICENSE" ||
    relativePath === "packages/eve/NOTICE" ||
    relativePath === "packages/eve/docs" ||
    OUTPUT_DIRS.has(relativePath)
  );
}
export async function listEveTreeFiles(
  root: string,
  kind: EveTreeKind,
): Promise<string[]> {
  const files: string[] = [];
  async function visit(directory: string, prefix = ""): Promise<void> {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const relative = prefix === "" ? entry.name : `${prefix}/${entry.name}`;
      if (excluded(relative, kind)) continue;
      const absolutePath = path.join(directory, entry.name);
      if (entry.isDirectory()) await visit(absolutePath, relative);
      else if (entry.isFile()) files.push(relative);
      else throw new Error(`Unsupported tree entry: ${relative}`);
    }
  }
  await visit(path.resolve(root));
  return files.sort();
}
export async function hashEveTree(
  root: string,
  kind: EveTreeKind,
): Promise<string> {
  const files = await listEveTreeFiles(root, kind);
  const hash = createHash("sha256");
  for (const relativePath of files) {
    const file = path.join(root, ...relativePath.split("/"));
    const [stat, bytes] = await Promise.all([lstat(file), readFile(file)]);
    const header = `${Buffer.byteLength(relativePath)}:${relativePath}\0${(stat.mode & 0o111) === 0 ? "0" : "1"}\0${bytes.length}\0`;
    hash.update(header).update(bytes).update("\0");
  }
  return hash.digest("hex");
}
const entry = process.argv[1];
if (entry !== undefined && import.meta.url === pathToFileURL(entry).href) {
  const [kind, root] = process.argv.slice(2);
  if ((kind !== "source" && kind !== "packed") || root === undefined) {
    throw new Error("Usage: node eve-tree-hash.ts <source|packed> <root>");
  }
  process.stdout.write(`${await hashEveTree(root, kind)}\n`);
}
