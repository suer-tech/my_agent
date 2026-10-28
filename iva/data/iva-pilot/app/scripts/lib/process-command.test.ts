/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registration promises */
import test from "node:test";
import assert from "node:assert/strict";
import { isIvaProcess, processCommand } from "./process-command.ts";

/**
 * Команда живого процесса читается: на ней стоят и замок обновления, и заявка на
 * обновление шима - они отличают своего владельца от чужого, занявшего тот же pid
 * после перезагрузки.
 */
test("the command of a live process is read", () => {
  const command = processCommand(process.pid);
  assert.match(command, /node|bun/iu, command);
});

/** Спросить не у кого: несуществующий pid - пустая строка, не исключение. */
test("a pid nobody runs has no command", () => {
  // Заведомо свободный номер: pid выше системного максимума не выдаётся никому.
  assert.equal(processCommand(4_294_967_295), "");
});

test("an Iva process is told from somebody else's", () => {
  assert.equal(
    isIvaProcess("/usr/bin/node /home/user/bin/iva.mjs update"),
    true,
  );
  assert.equal(isIvaProcess("/usr/bin/node update-finish.ts"), true);
  assert.equal(isIvaProcess("/usr/sbin/cron -f"), false);
});
