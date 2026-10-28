import { writeFileSync } from "node:fs";
import { rename } from "node:fs/promises";

import { acknowledgeQueueItems } from "../lib/telegram-queue.ts";

const [queueFileArg, markerFileArg] = process.argv.slice(2);
if (!queueFileArg || !markerFileArg) {
  throw new Error(
    "usage: telegram-queue-ack-crash-child <queue-file> <marker-file>",
  );
}
const queueFile = queueFileArg;
const markerFile = markerFileArg;

// Пачка из трёх id снимается одной записью; SIGKILL приходит сразу после публикации.
await acknowledgeQueueItems(queueFile, "1:", [101, 102, 103], {
  renameImpl: async (from, to) => {
    await rename(from, to);
    if (String(to) !== queueFile) return;
    writeFileSync(markerFile, "queue removal published\n", { mode: 0o600 });
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0);
  },
});
