import { createHash, randomBytes } from "node:crypto";
import {
  closeSync,
  fsyncSync,
  linkSync,
  openSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

// До возврата файл копии и каталог синхронизированы: следующая запись
// хранилища уже может убрать испорченную запись.
export function quarantineDamagedRecord(
  file: string,
  entry: { reason: string; record: unknown; label: string },
  log: (...parts: unknown[]) => void = console.error,
): string | null {
  const text = JSON.stringify(entry);
  const digest = createHash("sha256").update(text).digest("hex").slice(0, 16);
  const path = `${file}.damaged-${digest}.json`;
  const tmp = `${path}.tmp-${process.pid}-${randomBytes(4).toString("hex")}`;
  const fd = openSync(tmp, "wx", 0o600);
  try {
    try {
      writeFileSync(fd, text, "utf8");
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    try {
      linkSync(tmp, path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EEXIST") return null;
      throw error;
    }
    const directory = openSync(dirname(file), "r");
    try {
      fsyncSync(directory);
    } finally {
      closeSync(directory);
    }
  } finally {
    unlinkSync(tmp);
  }
  const type = (v: unknown) =>
    v === null ? "null" : Array.isArray(v) ? "array" : typeof v;
  const fields =
    entry.record !== null && typeof entry.record === "object"
      ? Object.entries(entry.record).map(([key, value]) => [key, type(value)])
      : type(entry.record);
  log(
    `damaged ${entry.label} copied to ${path}: ${entry.reason}`,
    JSON.stringify(fields),
  );
  return path;
}
