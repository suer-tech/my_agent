import { isHelperTurn } from "../lib/eve-auth.ts";
// Каждый ход: незакрытые провалы расписаний и напоминаний за сутки (T20 п.3).
// Динамическая инструкция, как 30-owner-rules.ts: читает data/jobs.json и таблицу
// напоминаний на turn.started. Нет провалов — пустая строка, ничего не стоит.
import { defineDynamic, defineInstructions } from "eve/instructions";
import {
  brokenFailureSourceMarkdown,
  openFailures,
  openFailuresMarkdown,
} from "../lib/open-failures.ts";

export { openFailuresMarkdown } from "../lib/open-failures.ts";

export default defineDynamic({
  events: {
    "turn.started": async (_event, ctx) => {
      if (isHelperTurn(ctx)) return null;
      // Битая таблица фактов или напоминаний — тоже провал, о котором агент должен
      // узнать: он получает причину текстом и чинит сам, ход из-за неё не падает.
      try {
        const failures = await openFailures();
        return defineInstructions({ markdown: openFailuresMarkdown(failures) });
      } catch (error) {
        return defineInstructions({
          markdown: brokenFailureSourceMarkdown(error),
        });
      }
    },
  },
});
