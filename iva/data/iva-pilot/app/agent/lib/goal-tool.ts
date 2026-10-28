import { helperAuth, type HelperToolContext } from "./helper-tool.ts";
import { isHelperTurn } from "./eve-auth.ts";
import * as r from "./goal-rules.ts";
import { changeGoal, listGoals } from "./goal-store.ts";
import { runHelperTick } from "./helper-tick.ts";
import { HelperStoreError } from "./helper-store.ts";
import { isQuietHour, parseProactive } from "./proactive-config.ts";
import { readSettings } from "./settings.ts";
import { zonedParts } from "./zoned-time.ts";
import { ownerTimeZone } from "./reminder-time.ts";
export async function goalExecute(
  input: unknown,
  ctx: HelperToolContext,
  deps: { now?: number; tick?: typeof runHelperTick } = {},
) {
  const parsed = r.goalInputSchema.safeParse(input);
  if (!parsed.success) return parsed.error.message;
  const v = parsed.data,
    now = deps.now ?? Date.now(),
    chat = helperAuth(ctx);
  const pair = [ctx?.session?.auth?.initiator, ctx?.session?.auth?.current];
  const helper = isHelperTurn(ctx) ? pair[0]?.attributes?.iva_helper : null;
  if (!chat && !pair.every((a) => a?.authenticator === "iva-bearer"))
    return "goal доступен из личного хода владельца или служебного хода Ивы.";
  if (
    helper !== null &&
    (typeof helper !== "string" ||
      !pair.every(
        (a) =>
          a?.attributes?.iva_turn === "helper" &&
          a.attributes.iva_helper === helper,
      ))
  )
    return "Эта проверка не совпадает с ходом помощника.";
  if (helper ? v.action !== "verdict" : v.action === "verdict")
    return "Только проверка goal может записать verdict; проверке доступен только verdict.";
  if (v.action === "start" && v.may === undefined)
    return "Назови границы: что помощнику можно от имени владельца, или пустую строку.";
  try {
    if (v.action === "list")
      return JSON.stringify({
        untrusted: true,
        goals: await listGoals(chat, now),
      });
    const goal = await changeGoal(v, chat, helper, now);
    if (v.action !== "verdict")
      void (deps.tick ?? runHelperTick)({ nowMs: now }).catch(console.error);
    if (v.action === "close")
      return JSON.stringify({ id: goal.id, closed: true });
    const config = parseProactive(readSettings());
    return JSON.stringify({
      id: goal.id,
      may: goal.may || "only looks and drafts, sends nothing",
      nextCheckAtMs: goal.nextCheckAtMs,
      quietHour: isQuietHour(config, zonedParts(now, ownerTimeZone()).hh),
      quietToHour: config.quietToHour,
      checks_today: goal.checks.day === r.goalDay(now) ? goal.checks.count : 0,
      checksPerDay: r.GOAL_CHECKS_PER_DAY,
      intervalMs: r.GOAL_DEFAULT_INTERVAL_MS,
    });
  } catch (error) {
    return error instanceof HelperStoreError
      ? "таблица помощников повреждена, помощники не работают"
      : String(error);
  }
}
