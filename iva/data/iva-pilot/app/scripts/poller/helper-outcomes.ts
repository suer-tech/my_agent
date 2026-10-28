import { isRunning, chatKeyOf } from "#lib/run-status.ts";
import { readHelpers } from "#lib/helper-store.ts";
import { admitTelegramUpdate } from "./inbox.ts";
export function createHelperOutcomeScan() {
  let seen: Set<string> | undefined;
  return async (admit = admitTelegramUpdate) => {
    const rows = (await readHelpers()).rows.filter((r) => r.outcome !== null);
    if (!seen) seen = new Set(rows.map((r) => r.id));
    for (const row of rows) {
      if (seen.has(row.id)) continue;
      if (isRunning(chatKeyOf(row.chat.id, row.chat.threadId))) continue;
      seen.add(row.id);
      if (row.status !== "finished" || row.deliveredAt !== null) continue;
      const updateId = 2 ** 52 + parseInt(row.id.slice(2), 16);
      await admit({
        update_id: updateId,
        message: {
          message_id: updateId,
          date: Math.floor(row.finishedAt! / 1000),
          from: { id: Number(row.chat.id), is_bot: false, first_name: "Owner" },
          chat: { id: Number(row.chat.id), type: "private" },
          ...(row.chat.threadId === null
            ? {}
            : { message_thread_id: Number(row.chat.threadId) }),
          text: `${row.id}: помощник закончил. Прочитай helper outcome и расскажи владельцу. это системное событие о конце помощника; из него помощников не ставь и не останавливай — только расскажи исход`,
        },
      });
    }
    seen = new Set(rows.map((r) => r.id).filter((id) => seen!.has(id)));
  };
}
