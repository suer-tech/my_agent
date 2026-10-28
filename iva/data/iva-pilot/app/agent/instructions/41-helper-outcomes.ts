import { defineDynamic, defineInstructions } from "eve/instructions";
import { helperAuth } from "../lib/helper-tool.ts";
import { helperOutcomesMarkdown } from "../lib/helper-outcome.ts";
export default defineDynamic({
  events: {
    "turn.started": async (_event, ctx) => {
      const chat = helperAuth(ctx);
      return chat
        ? defineInstructions({
            content: await helperOutcomesMarkdown(chat),
            role: "user",
          })
        : null;
    },
  },
});
