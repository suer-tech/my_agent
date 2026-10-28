// In an archived 21f447ed tree with eve iva.1 and these two fixture scripts copied in: node scripts/fixtures/helper-turn-snapshot.ts > helper-turn-base.snapshot.json && mv helper-turn-base.snapshot.json scripts/fixtures/helper-turn-base.json
import "../lib/ts-esm-hooks.ts";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { wireTurn } from "./helper-turn.test-fixture.ts";
const home = await mkdtemp(join(tmpdir(), "iva-base-snapshot-"));
process.env.ASSISTANT_VAULT_DIR = join(home, "vault");
process.env.ASSISTANT_DATA_DIR = join(home, "data");
process.env.MODEL_PROVIDER = "ollama";
process.env.AGENT_LANGUAGE = "en";
process.env.ASSISTANT_TIMEZONE = "UTC";
try {
  for (const dir of ["vault", "data/custom/agent/instructions"])
    await mkdir(join(home, dir), { recursive: true });
  for (const [file, text] of [
    ["vault/CORE.md", "CORE_FIXTURE owner project"],
    ["vault/PERSONA.md", "PERSONA_FIXTURE concise"],
    [
      "data/custom/agent/instructions/rule.md",
      "OWNER_FIXTURE use dated sources",
    ],
  ])
    await writeFile(join(home, file), text);
  const wire = JSON.stringify((await wireTurn()).wire);
  for (const kind of "reminder signal watch brief insight alert".split(" "))
    if (JSON.stringify((await wireTurn({ kind })).wire) !== wire)
      throw new Error(`base wire differs: ${kind}`);
  const { proxyMetadata } = JSON.parse(
    await readFile(new URL("./helper-turn-base.json", import.meta.url), "utf8"),
  ) as { proxyMetadata: string };
  console.log(
    JSON.stringify({ base: "21f447ed", wire, proxyMetadata }, null, 2),
  );
} finally {
  await rm(home, { recursive: true, force: true });
}
