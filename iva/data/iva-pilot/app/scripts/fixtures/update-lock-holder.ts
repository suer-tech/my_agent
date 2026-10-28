import { writeFileSync } from "node:fs";
import { acquireUpdateLock, adoptUpdateLock } from "../lib/version-store.ts";

const [data, ready] = process.argv.slice(2);
if (!data || !ready) throw new Error("update lock holder needs data and ready");
const lock =
  process.argv[4] === "adopt" ? adoptUpdateLock(data) : acquireUpdateLock(data);
writeFileSync(
  ready,
  JSON.stringify({ pid: process.pid, acquired: lock !== null }),
);
setInterval(() => {}, 1_000);
