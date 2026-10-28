import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

/**
 * Команда процесса по его pid, пустая строка - если спросить не удалось.
 *
 * Нужна везде, где pid записан на диск и переживает перезагрузку: после неё тот же
 * номер носит чужой процесс, и одного `kill(pid, 0)` мало, чтобы считать владельца
 * живым. Читатели: заявка на обновление шима (version-layout) и замок обновления
 * (version-store).
 */
export function processCommand(pid: number): string {
  try {
    return readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0").join(" ");
  } catch {
    // Не Linux или /proc закрыт — спросим ps.
  }
  try {
    return execFileSync("ps", ["-o", "command=", "-p", String(pid)], {
      encoding: "utf8",
    });
  } catch {
    return "";
  }
}

/** Наш ли это процесс: шим, CLI установки или вторая половина обновления. */
export function isIvaProcess(command: string): boolean {
  return /iva/iu.test(command) || /update-finish/u.test(command);
}
