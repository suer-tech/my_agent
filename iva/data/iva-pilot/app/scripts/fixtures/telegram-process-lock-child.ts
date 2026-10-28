import { randomUUID } from "node:crypto";
import { renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [mode, dataDir, botId = "71020", guardDirectory, guardIdentity] =
  process.argv.slice(2);
if (!mode || !dataDir || !guardDirectory || !guardIdentity) {
  throw new Error(
    "usage: child <hold|write-on-signal|kill-holder> <data-dir> <bot-id> <guard-directory> <guard-identity>",
  );
}
process.env.ASSISTANT_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = `${botId}:test-token`;
if (mode === "write-on-signal") {
  process.on("SIGUSR1", () => {
    // The name appears only once the bytes are all there: the staging name is not the
    // one the test looks for, and the rename is atomic inside the directory.
    const writer = join(dataDir, "active-writer");
    const staged = join(dataDir, `.active-writer.tmp-${randomUUID()}`);
    writeFileSync(staged, `${process.pid}\n`);
    renameSync(staged, writer);
  });
}

try {
  const { acquireTelegramProcessLock } = (await import(
    `../poller/process-lock.ts?child=${process.pid}`
  )) as unknown as {
    acquireTelegramProcessLock: (options: {
      testGuard: { identity: string; directory: string };
    }) => Promise<{
      owner: unknown;
      resource: string;
      guardRoot: string;
      lockFile: string;
      guardOwnerFile: string;
      holderPid: number;
    }>;
  };
  const lease = await acquireTelegramProcessLock({
    testGuard: { identity: guardIdentity, directory: guardDirectory },
  });
  process.stdout.write(
    `${JSON.stringify({
      event: "READY",
      owner: lease.owner,
      resource: lease.resource,
      guardRoot: lease.guardRoot,
      lockFile: lease.lockFile,
      guardOwnerFile: lease.guardOwnerFile,
      holderPid: lease.holderPid,
    })}\n`,
  );
  if (mode === "kill-holder") {
    process.kill(lease.holderPid, "SIGKILL");
  } else if (mode !== "hold" && mode !== "write-on-signal") {
    throw new Error(`unknown mode: ${mode}`);
  }
  setInterval(() => {}, 60_000);
} catch (error) {
  console.error(error);
  process.exit(1);
}
