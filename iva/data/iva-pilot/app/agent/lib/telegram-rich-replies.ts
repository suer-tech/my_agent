// TELEGRAM_RICH_REPLIES — операторский тумблер rich-ответов в текущем чате.
// auto (дефолт) оставляет прежнее поведение: таблица, таск-лист, <details> или
// блочная формула уходят в Telegram rich-сообщением. never держит такие ответы на
// обычном HTML/plain-пути. Любое другое значение — отказ при старте: подменить его
// дефолтом значит молча разойтись с настройкой оператора.
// Константа вычисляется при импорте (как порог retire в agent/hooks/trace.ts),
// поэтому кривое значение валит импорт, то есть старт eve, а не первый ответ.
// Отдельный файл, а не строка внутри Канала: Канал не импортируется без токена
// бота, а отказ при старте проверяется импортом этого файла голым node.
export type RichReplies = "auto" | "never";

export function richRepliesMode(raw: string | undefined): RichReplies {
  if (raw === undefined || raw === "auto") return "auto";
  if (raw === "never") return "never";
  throw new Error(
    `TELEGRAM_RICH_REPLIES must be "auto" or "never", got ${JSON.stringify(raw)}`,
  );
}

export const TELEGRAM_RICH_REPLIES = richRepliesMode(
  process.env.TELEGRAM_RICH_REPLIES,
);
