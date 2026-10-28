/* eslint-disable @typescript-eslint/no-floating-promises -- Node test registration. */
import "./fixtures/no-host-anthropic.ts";
import "./lib/ts-esm-hooks.ts";
import assert from "node:assert/strict";
import test from "node:test";
import { wireTurn } from "./fixtures/helper-turn.test-fixture.ts";
import { getPendingInputRequestIds } from "../node_modules/eve/dist/src/harness/pending-input-batches.js";
process.env.MODEL_PROVIDER = "ollama";
process.env.OLLAMA_MODEL = "chat-fixture";
for (const mode of ["conversation", "task"] as const)
  for (const noEvents of [false, true])
    for (const approval of [false, true])
      test(`pending response plus message preserves five calls: ${mode}/${noEvents}/${approval}`, async () => {
        const turn = await wireTurn({
          mode,
          noEvents,
          attributes: { iva_max_steps: "5" },
          ...(approval ? { approvalTool: "read_file" } : {}),
          sequence: [
            "web_fetch",
            approval ? "read_file" : "ask_question",
            "web_fetch",
            "web_fetch",
            "web_fetch",
            "web_fetch",
            "web_fetch",
            "done",
          ],
        });
        const [requestId] = getPendingInputRequestIds(
          turn.result.session.state,
        );
        assert.ok(requestId);
        const response = { requestId, optionId: approval ? "approve" : "one" };
        let result = await turn.authored.eve.contextStorage.run(turn.ctx, () =>
          turn.run(turn.result.session, {
            message: "continue with this detail",
            ...(approval
              ? { inputResponses: [response] }
              : {
                  attributedInputResponses: [
                    { auth: turn.authored.auth!, response },
                  ],
                }),
          }),
        );
        for (let i = 0; typeof result.next === "function" && i < 12; i++) {
          const next = result.next;
          result = await turn.authored.eve.contextStorage.run(turn.ctx, () =>
            next(result.session),
          );
        }
        const calls = [
          ...turn.model.doStreamCalls,
          ...turn.model.doGenerateCalls,
        ];
        assert.equal(calls.length, 5);
        assert.equal(calls.at(-1)?.tools?.length ?? 0, 0);
        assert.equal(result.session.state?.["eve.harness.modelCalls"], 5);
      });
