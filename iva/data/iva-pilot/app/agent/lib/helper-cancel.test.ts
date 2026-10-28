/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns test registration. */
import test from "node:test";
import assert from "node:assert/strict";
import { Client } from "eve/client";
test("authored client cancels an exact id through the existing HTTP seam", async (t) => {
  const requests: { url: string; body: unknown; headers: Headers }[] = [];
  t.mock.method(globalThis, "fetch", (url: string, init: RequestInit) => {
    requests.push({
      url: String(url),
      body: JSON.parse(
        typeof init.body === "string" ? init.body : "null",
      ) as unknown,
      headers: new Headers(init.headers),
    });
    return Promise.resolve(
      Response.json({ ok: true, status: "no_active_turn" }),
    );
  });
  const client = new Client({
    host: "http://fixture.invalid",
    auth: { bearer: "fixture" },
  });
  assert.deepEqual(
    await client.sessions
      .attach("helper/probe")
      .cancel({ tasks: true, signal: AbortSignal.timeout(1000) }),
    { status: "no_active_turn" },
  );
  assert.equal(
    requests[0].url,
    "http://fixture.invalid/eve/v1/session/helper%2Fprobe/cancel",
  );
  assert.deepEqual(requests[0].body, { tasks: true });
  assert.equal(requests[0].headers.get("authorization"), "Bearer fixture");
});
