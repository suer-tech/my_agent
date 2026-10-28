import "../lib/ts-esm-hooks.ts";
import { wireTurn } from "./helper-turn.test-fixture.ts";
import { appendPendingInputBatch } from "../../node_modules/eve/dist/src/harness/pending-input-batches.js";

// Golden: git archive 21f447ed | tar -xf - -C "$base"
// Install the iva.1 archive from that tree's package-lock.json after verifying
// its sha512 integrity; link the other dependencies. Copy this fixture,
// helper-turn.test-fixture.ts and helper-turn-base.json into base/scripts/fixtures.
// In that tree (Node 24, MODEL_PROVIDER=ollama OLLAMA_MODEL=chat-fixture):
// node --input-type=module -e 'import "./scripts/lib/ts-esm-hooks.ts";
// const {eventSnapshot}=await import("./scripts/fixtures/helper-events-r3.test-fixture.ts");
// console.log(JSON.stringify({base:"21f447ed",archive:"0.51.1-iva.1",cases:await eventSnapshot()},null,2));'
export const eventCases = [
  "question-response",
  "question-response-message",
  "question-message",
  "stale-response",
  "approval-cancel",
  "context",
  "plain",
  "schema-failure",
  "schema-success",
  "compaction",
] as const;
export type EventCase = (typeof eventCases)[number];

export async function eventProbe(
  mode: "conversation" | "task",
  scenario: EventCase,
  full = false,
) {
  const question = scenario.startsWith("question");
  const turn = await wireTurn({
    mode,
    attributes: {},
    sequence: question
      ? ["read_file", "ask_question", "read_file", "done"]
      : scenario === "schema-success"
        ? ["final_output"]
        : ["done", "done"],
    compact: scenario === "compaction",
    ...(scenario.startsWith("schema-")
      ? {
          outputSchema: {
            type: "object",
            properties: { answer: { type: "string" } },
          },
        }
      : {}),
  });
  const initial = turn.events.map((e) => e.type);
  let result = turn.result;
  const index = turn.events.length;
  if (
    question ||
    ["stale-response", "approval-cancel", "context"].includes(scenario)
  ) {
    let session = result.session;
    if (scenario === "approval-cancel")
      session = appendPendingInputBatch({
        session,
        requests: [
          {
            kind: "tool-approval",
            requestId: "approval-1",
            prompt: "Approve",
            action: {
              kind: "tool-call",
              callId: "approval-1",
              toolName: "read_file",
              input: { path: "CORE.md" },
            },
          },
        ],
        responseMessages: [
          {
            role: "assistant",
            content: [
              {
                type: "tool-call",
                toolCallId: "approval-1",
                toolName: "read_file",
                input: { path: "CORE.md" },
              },
              {
                type: "tool-approval-request",
                approvalId: "approval-1",
                toolCallId: "approval-1",
              },
            ],
          },
        ],
      });
    const input =
      scenario === "context"
        ? { context: ["new independent task"] }
        : scenario === "question-message"
          ? { message: "One" }
          : {
              inputResponses: [
                {
                  requestId:
                    scenario === "approval-cancel"
                      ? "approval-1"
                      : scenario === "stale-response"
                        ? "stale"
                        : "t2",
                  optionId: scenario === "approval-cancel" ? "cancel" : "one",
                },
              ],
              ...(scenario === "question-response-message"
                ? { message: "supplement" }
                : {}),
            };
    result = await turn.authored.eve.contextStorage.run(turn.ctx, () =>
      turn.run(session, input),
    );
    while (typeof result.next === "function") {
      const next = result.next;
      result = await turn.authored.eve.contextStorage.run(turn.ctx, () =>
        next(result.session),
      );
    }
  }
  const calls = [...turn.model.doStreamCalls, ...turn.model.doGenerateCalls];
  return {
    ...(full
      ? {
          history: result.session.history,
          state: result.session.state,
          payloads: turn.events,
          wire: turn.wire,
        }
      : {}),
    initial,
    resumed: turn.events.slice(index).map((e) => e.type),
    calls: calls.length,
    tools: calls.map((call) => call.tools?.length ?? 0),
    limitNote: calls.some((call) =>
      JSON.stringify(call.prompt).includes("Step limit reached:"),
    ),
    outcome:
      typeof result.next === "object" && result.next !== null
        ? result.next
        : (result.settledTurn ?? null),
    threshold: result.session.compaction.threshold,
    thresholdPercent: result.session.compaction.thresholdPercent ?? null,
  };
}

export async function eventSnapshot() {
  const cases: Record<string, Awaited<ReturnType<typeof eventProbe>>> = {};
  for (const mode of ["conversation", "task"] as const)
    for (const scenario of eventCases)
      cases[`${mode}/${scenario}`] = await eventProbe(mode, scenario);
  return cases;
}
