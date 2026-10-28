import { existsSync } from "node:fs";
import * as s from "./helper-store.ts";
import {
  sanitizeInbound,
  hasInboundAttackSignal,
  scanOutbound,
} from "./security-gate.ts";
import { injectionWarning } from "./telegram-gate-notice.ts";
type TurnKey = Record<"sessionId" | "turnId", string>;
const reads = new Map<
  string,
  { ids: Set<string>; reply: boolean; chat: s.HelperChat; sessionId: string }
>();
const turnKey = (key: TurnKey) => JSON.stringify([key.sessionId, key.turnId]);
export function noteHelperReply(key: TurnKey, text: string) {
  const entry = reads.get(turnKey(key));
  if (entry && text.trim()) entry.reply = true;
}
export function discardHelperOutcomes(sessionId: string, turnId?: string) {
  const match = turnId ? turnKey({ sessionId, turnId }) : null;
  for (const [key, entry] of reads)
    if (entry.sessionId === sessionId && (!match || key === match))
      reads.delete(key);
}
export async function finishHelperOutcomes(key: TurnKey, now: number) {
  const entry = reads.get(turnKey(key));
  reads.delete(turnKey(key));
  if (entry?.reply)
    for (const id of entry.ids) {
      try {
        await s.markHelperDelivered(id, entry.chat, now);
      } catch (error) {
        console.error("[helper] delivered:", error);
      }
    }
}
export async function readHelperOutcome(
  id: string,
  chat: s.HelperChat,
  key: TurnKey,
): Promise<string> {
  const result = await s.showHelper(id, chat);
  if (!result.ok) return result.text;
  const r = result.row;
  if (r.outcome === null)
    return "Останавливается, частичный итог будет после остановки.";
  const content = [r.task.slice(0, 300), r.summary, r.reason].map((v, i) =>
    v === null ? null : sanitizeInbound(i ? scanOutbound(v).text : v),
  );
  const path = s.helperResultPath(id);
  const envelope = JSON.stringify({
    type: "helper_outcome",
    untrusted: true,
    id,
    task: content[0]?.text,
    outcome: r.outcome,
    summary: content[1]?.text ?? null,
    full_text_path: existsSync(path) ? path : null,
    reason: content[2]?.text ?? null,
    asked_at: new Date(r.createdAt).toISOString(),
    finished_at: new Date(r.finishedAt!).toISOString(),
  });
  if (r.status === "finished" && r.deliveredAt === null) {
    const entry = reads.get(turnKey(key)) ?? {
      ids: new Set<string>(),
      reply: false,
      chat,
      sessionId: key.sessionId,
    };
    entry.ids.add(id);
    reads.set(turnKey(key), entry);
  }
  return (
    (content.some((v) => v && hasInboundAttackSignal(v))
      ? injectionWarning() + "\n"
      : "") + envelope
  );
}
export async function helperOutcomesMarkdown(chat: s.HelperChat) {
  try {
    const state = await s.readHelpers();
    return [
      ...state.rows
        .filter((r) => s.sameChat(r.chat, chat) && s.ready(r))
        .map(
          (r) =>
            `${r.id}, ${new Date(r.finishedAt!).toISOString()}: прочитай helper outcome и расскажи.`,
        ),
      ...state.goals
        .filter((g) => s.sameChat(g.chat, chat) && g.pause === "expired")
        .map(
          (g) =>
            `goal ${g.id}: paused (expired). Tell the owner once and offer to continue.`,
        ),
    ].join("\n");
  } catch (error) {
    console.error("[helper] outcomes:", error);
    return "таблица помощников повреждена, помощники не работают";
  }
}
