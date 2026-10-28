import { defineDynamic, defineInstructions } from "eve/instructions";
import { ownerRulesDir, ownerRulesMarkdown } from "../lib/owner-rules.ts";

export { ownerRulesMarkdown } from "../lib/owner-rules.ts";

// Динамическая инструкция: правила владельца лежат markdown-файлами в
// data/custom/agent/instructions/ и перечитываются каждый ход — «запомни правило» действует
// со следующего хода, без сборки и рестарта. Сборка эти файлы не копирует
// (scripts/lib/authored-paths.ts: isLiveInstructionPath), пишет их write_file.
export default defineDynamic({
  events: {
    "turn.started": () =>
      defineInstructions({ markdown: ownerRulesMarkdown(ownerRulesDir()) }),
  },
});
