/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns registration. */
import "./lib/ts-esm-hooks.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
test("v7 helpers schedule completes while the reminder task is still waiting, with separate Nitro task names", async () => {
  const { default: helper } = await import("../agent/schedules/helpers.ts");
  assert.equal(helper.cron, "* * * * *");
  const run = promisify(execFile);
  const script = `
    import './scripts/lib/ts-esm-hooks.ts';
    import { registerHooks } from 'node:module';
    registerHooks({ load(url, ctx, next) {
      if (url.endsWith('/agent/lib/reminder-tick.ts')) return { format:'module', source:'export const runReminderTick = () => new Promise(() => {});', shortCircuit:true };
      if (url.endsWith('/agent/lib/helper-tick.ts')) return { format:'module', source:'export const runHelperTick = () => Promise.resolve("helper tick completed");', shortCircuit:true };
      return next(url, ctx);
    }});
    const reminder = (await import('./agent/schedules/reminders.ts')).default;
    const helper = (await import('./agent/schedules/helpers.ts')).default;
    let held, done;
    reminder.run({ waitUntil: p => held=p }); helper.run({ waitUntil: p => done=p });
    if (helper.cron !== '* * * * *') throw Error('cron changed');
    if (await done !== 'helper tick completed') throw Error('helper waited for reminder');
    const { createScheduleRegistrations } = await import('./node_modules/eve/dist/src/runtime/schedules/register.js');
    const entries = createScheduleRegistrations(['helpers','reminders'].map(name => ({ name, sourceId:'agent/schedules/'+name, logicalPath:'agent/schedules/'+name+'.ts', cron:'* * * * *' })));
    if (entries.length !== 2 || entries[0].taskName === entries[1].taskName) throw Error('Nitro task shared');
    console.log(JSON.stringify(entries.map(e=>e.taskName)));
  `;
  const result = await run(process.execPath, [
    "--input-type=module",
    "-e",
    script,
  ]);
  const names: unknown = JSON.parse(result.stdout);
  assert.ok(Array.isArray(names));
  assert.equal(names.length, 2);
});
test("v7 owner epoch is process-wide across duplicate module loads", async (t) => {
  const { OWNER_STARTED_AT: a } =
    await import("../agent/lib/schedule-runner.ts");
  t.mock.timers.enable({ apis: ["Date"], now: Date.now() + 1000 });
  const path = new URL(
    "../agent/lib/schedule-runner.ts?independent",
    import.meta.url,
  ).href;
  const b = (await import(path)) as { OWNER_STARTED_AT: number };
  assert.equal(a, b.OWNER_STARTED_AT);
});
test("v7 helper header uses the common reminder client and preserves R1 failure", async () => {
  const { runReminderTurn } = await import("./lib/reminder-turn.ts");
  const options = {
    host: "http://fixture.invalid",
    auth: { bearer: () => Promise.resolve("fixture") },
    headers: { "x-iva-turn": "helper" },
  };
  const resets: string[] = [];
  const response = Object.assign(
    (async function* () {
      await Promise.resolve();
      yield { type: "message.completed", data: { message: "intermediate" } };
      yield { type: "turn.failed" };
      yield { type: "session.waiting" };
    })(),
    {
      sessionId: "fixture-session",
      cancel: () => Promise.reject(new Error("unexpected stream cancellation")),
    },
  );
  const turn = await runReminderTurn("fixture", options, {
    createClient: (received) => {
      assert.equal(received, options);
      return Promise.resolve({
        sessions: {
          create: ({ message }) => {
            assert.equal(message, "fixture");
            return Promise.resolve({
              response,
              session: {
                send: () => Promise.resolve(),
                cancel: () =>
                  Promise.reject(new Error("unexpected session cancellation")),
                reset: ({ reason }) => {
                  resets.push(reason);
                  return Promise.resolve();
                },
              },
            });
          },
        },
      });
    },
  });
  assert.equal(turn.status, "failed");
  assert.equal(turn.message, "ход упал");
  assert.equal(turn.cancelled, false);
  assert.deepEqual(resets, ["Reminder finished"]);
});
