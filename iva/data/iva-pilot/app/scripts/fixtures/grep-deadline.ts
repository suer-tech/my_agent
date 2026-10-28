import "../lib/ts-esm-hooks.ts";
import type { ToolContext } from "eve/tools";
const { default: grep } = await import("../../agent/tools/grep.ts");
const controller = new AbortController();
let ticks = 0;
const ticker = setInterval(() => {
  ticks++;
}, 10);
const timer =
  process.argv[3] === "abort"
    ? setTimeout(() => controller.abort(), 80)
    : undefined;
try {
  const result = await grep.execute(
    { pattern: "(a+)+$", path: process.argv[2] },
    { abortSignal: controller.signal } as ToolContext,
  );
  console.log(JSON.stringify({ result, ticks }));
} finally {
  clearInterval(ticker);
  clearTimeout(timer);
}
