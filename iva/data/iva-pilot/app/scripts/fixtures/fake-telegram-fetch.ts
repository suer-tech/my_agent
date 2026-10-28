import { appendFileSync } from "node:fs";

const realFetch = globalThis.fetch;

globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = input instanceof Request ? input.url : String(input);
  if (url.startsWith("https://api.telegram.org/")) {
    const log = process.env.QA_T17_SEND_LOG;
    const body = typeof init?.body === "string" ? init.body : "";
    if (log) appendFileSync(log, `${body}\n`);
    return new Response('{"ok":true,"result":{}}', {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  return realFetch(input, init);
};
