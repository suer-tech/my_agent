import "./fixtures/no-host-anthropic.ts";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test, { type TestContext } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import fc from "fast-check";
import { runDailyUpdateCheck } from "./check-update.ts";
import * as cli from "./cli/version-update-command.ts";
import { createCliRuntime } from "./cli/runtime.ts";
import * as check from "./lib/update-check.ts";
import {
  createVersionStore,
  parseVersionName,
  versionName,
} from "./lib/version-store.ts";
import { classifyRoot, upstreamQuery } from "./lib/version-layout.ts";
import {
  parseWhatsNew,
  whatsNewBetween,
  formatWhatsNew,
} from "./lib/whats-new.ts";
import { resolveDataDir } from "../packages/data-dir/index.ts";
import { resolveVaultDir } from "../packages/vault-dir/index.ts";

const ROOT = fileURLToPath(new URL("../", import.meta.url));
const TRANSITIONS = [
  ["0.4.17", "0.5.0"],
  ["0.4.9", "0.5.0"],
  ["0.5.0", "0.5.1"],
] as const;
const IDENTITY = {
  GIT_AUTHOR_NAME: "iva",
  GIT_AUTHOR_EMAIL: "iva@example.invalid",
  GIT_COMMITTER_NAME: "iva",
  GIT_COMMITTER_EMAIL: "iva@example.invalid",
};
function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", args, {
    cwd,
    encoding: "utf8",
    env: { ...process.env, ...IDENTITY },
  }).trim();
}
function workspace(t: TestContext): string {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "iva-version-050-")));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}
function write(root: string, path: string, body: string): void {
  mkdirSync(dirname(join(root, path)), { recursive: true });
  writeFileSync(join(root, path), body);
}
function readme(version: string): string {
  return `# Iva\n## What's New\n#### v${version}\n- 🔧 **Minor boundary**: release details\n`;
}
function releases(root: string, from: string, to: string) {
  const source = join(root, "source");
  mkdirSync(source, { recursive: true });
  git(source, "init", "-q", "-b", "main");
  const commit = (version: string) => {
    write(
      source,
      "package.json",
      JSON.stringify({ name: "iva", version, type: "module" }),
    );
    write(
      source,
      "update-compat.json",
      readFileSync(join(ROOT, "update-compat.json"), "utf8"),
    );
    write(source, "README.md", readme(version));
    write(source, "README.ru.md", readme(version));
    write(
      source,
      "CHANGELOG.md",
      `## [Unreleased]\n\n## [${version}] - 2026-10-15\n- 🔧 **Released headline**: details\n`,
    );
    git(source, "add", "-A");
    git(source, "commit", "-qm", version);
    git(source, "tag", `v${version}`);
    return git(source, "rev-parse", "HEAD");
  };
  const before = commit(from);
  const after = commit(to);
  const remote = join(root, "remote.git");
  git(root, "clone", "-q", "--bare", source, remote);
  return { source, remote, before, after };
}

void test("version comparison crosses arbitrary minor boundaries in both directions (seed 500050)", () => {
  fc.assert(
    fc.property(
      fc.record({
        major: fc.nat({ max: 30 }),
        minor: fc.nat({ max: 1000 }),
        oldPatch: fc.nat({ max: 10000 }),
        nextPatch: fc.nat({ max: 10000 }),
        gap: fc.integer({ min: 1, max: 1000 }),
      }),
      ({ major, minor, oldPatch, nextPatch, gap }) => {
        const older = `${major}.${minor}.${oldPatch}`;
        const newer = `${major}.${minor + gap}.${nextPatch}`;
        assert.equal(check.compareStableVersions(older, newer), 1);
        assert.equal(check.compareStableVersions(newer, older), -1);
        assert.equal(check.compareStableVersions(newer, newer), 0);
      },
    ),
    { seed: 500050, numRuns: 500 },
  );
  for (const [from, to] of TRANSITIONS) {
    assert.equal(check.compareStableVersions(from, to), 1);
    assert.equal(check.compareStableVersions(to, from), -1);
    assert.equal(check.packageVersion(JSON.stringify({ version: to })), to);
    assert.equal(check.parseMinUpdater(JSON.stringify({ minUpdater: to })), to);
  }
});

void test("0.5 version directories, active state and numeric ordering preserve the installation anchor", (t) => {
  const home = workspace(t);
  const store = createVersionStore(home);
  const sha = "0123456789abcdef0123456789abcdef01234567";
  const versions = ["0.4.9", "0.4.17", "0.5.0", "0.5.1", "0.5.10"];
  for (const version of versions) {
    const name = versionName(version, sha);
    const dir = store.stage(name);
    write(dir, "package.json", JSON.stringify({ version }));
    store.linkState(dir);
    store.complete(name);
    assert.equal(parseVersionName(name)?.version, version);
  }
  assert.deepEqual(
    store.list().map((name) => parseVersionName(name)?.version),
    [...versions].reverse(),
  );
  const name = versionName("0.5.0", sha);
  store.activate(name);
  store.settle(name);
  const dir = join(home, "versions", name);
  assert.equal(store.currentName(), name);
  assert.equal(store.settled(), name);
  assert.equal(classifyRoot(dir).kind, "version");
  assert.equal(createCliRuntime(dir).gitHead(), sha.slice(0, 12));
  assert.equal(upstreamQuery(dir).head, sha.slice(0, 12));
  assert.equal(resolveDataDir(dir, undefined), join(home, "data"));
  assert.equal(
    realpathSync(resolveVaultDir(dir, undefined)),
    join(home, "vault"),
  );
  fc.assert(
    fc.property(
      fc.integer({ min: 1, max: 10000 }),
      fc.integer({ min: 1, max: 1000 }),
      (beta, build) => {
        const version = `0.5.0-beta.${beta}`;
        assert.deepEqual(
          parseVersionName(versionName(version, sha, "89abcdef", build)),
          { version, sha: sha.slice(0, 12), overlay: "89abcdef", build },
        );
      },
    ),
    { seed: 500051, numRuns: 200 },
  );
});

for (const [from, to] of TRANSITIONS) {
  void test(`${from} -> ${to}: real git release, compatibility marker and daily offer`, async (t) => {
    const root = workspace(t);
    const fx = releases(root, from, to);
    const mirror = join(root, "mirror.git");
    git(root, "clone", "-q", "--mirror", fx.remote, mirror);
    const target = await cli.resolveTarget(mirror, fx.before);
    assert.equal(target.version, to);
    assert.equal(target.sha, fx.after);
    assert.equal(target.release, `v${to}`);
    const info = await check.inspectUpstream({ root: mirror, head: fx.before });
    assert.equal(info.hasVersionUpdate, true);
    assert.equal(info.localVersion, from);
    assert.equal(info.remoteVersion, to);
    assert.equal(info.updaterTooOld, false);
    assert.equal((await cli.resolveTarget(mirror, fx.after)).sha, fx.after);
    const compatGit = (...args: string[]) => check.gitAt(mirror, args);
    assert.deepEqual(await check.updaterCompat(compatGit, fx.after, from), {
      status: "ok",
    });
    const env = {
      TELEGRAM_BOT_TOKEN: "fixture",
      TELEGRAM_ALLOWED_USER_IDS: "42",
      AGENT_LANGUAGE: "ru",
      ASSISTANT_DATA_DIR: join(root, "data"),
    };
    const sent: string[] = [];
    const saved: string[] = [];
    const options = {
      root: mirror,
      env,
      inspectImpl: () => Promise.resolve(info),
      sendImpl: ({ offer }: { offer: { text: string } }) => {
        sent.push(offer.text);
        return Promise.resolve();
      },
      readStateImpl: () => Promise.resolve(saved.at(-1) ?? null),
      writeStateImpl: (_dir: string, version: string) => {
        saved.push(version);
        return Promise.resolve();
      },
    };
    assert.equal((await runDailyUpdateCheck(options)).status, "notified");
    assert.equal(sent.length, 1);
    assert.ok(sent[0].includes(`v${from} → v${to}`));
    assert.ok(sent[0].includes("🔧 Minor boundary"));
    assert.ok(sent[0].includes('data="iva_update:do"'));
    assert.deepEqual(saved, [to]);
    assert.equal(
      (await runDailyUpdateCheck(options)).status,
      "already-notified",
    );
    assert.equal(sent.length, 1);
  });
}

void test("README news selects 0.5.0 and 0.5.1; CHANGELOG release headings close Unreleased", () => {
  const entries = parseWhatsNew(
    ["0.5.1", "0.4.17", "0.5.0", "0.4.9"].map(readme).join("\n"),
  );
  for (const [from, to] of TRANSITIONS) {
    const selection = whatsNewBetween(entries, from, to);
    assert.deepEqual(
      selection.versions.map((entry) => entry.version),
      from === "0.4.9" ? ["0.5.0", "0.4.17"] : [to],
    );
    assert.ok(formatWhatsNew(selection, "ru").includes(`v${to}`));
  }
  const changelog =
    "## [Unreleased]\n- 🔧 **Pending headline**: pending\n\n## [0.5.0] - 2026-10-15\n- 🔧 **Released headline**: released\n";
  const offer = check.betaOffer("0.5.0-beta.1", changelog, "ru");
  assert.ok(offer.text.includes("v0.5.0-beta.1"));
  assert.ok(offer.text.includes("Pending headline"));
  assert.equal(offer.text.includes("Released headline"), false);
});

/** Load the shipped files unchanged, including their relative imports and package aliases. */
async function shipped(t: TestContext, tag: string) {
  const root = workspace(t);
  execFileSync("tar", ["-xf", "-", "-C", root], {
    input: execFileSync(
      "git",
      ["archive", tag, "scripts", "agent", "packages", "package.json"],
      { cwd: ROOT, maxBuffer: 32 * 1024 * 1024 },
    ),
  });
  symlinkSync(join(ROOT, "node_modules"), join(root, "node_modules"));
  const file = "scripts/cli/version-update-command.ts";
  assert.equal(
    readFileSync(join(root, file), "utf8"),
    git(ROOT, "show", `${tag}:${file}`) + "\n",
  );
  return {
    cli: (await import(pathToFileURL(join(root, file)).href)) as typeof cli,
    check: (await import(
      pathToFileURL(join(root, "scripts/lib/update-check.ts")).href
    )) as typeof check,
  };
}

// The second half records the actual child process boundary; install/build/probe are
// covered separately. This fixture deliberately leaves the old active version serving.
const FINISH = `import { readFileSync, writeFileSync } from "node:fs";
const [home, name] = process.argv.slice(2);
const version = JSON.parse(readFileSync("package.json", "utf8")).version;
writeFileSync(home + "/handoff.json", JSON.stringify({ home, name, version, cwd: process.cwd(), entry: import.meta.url }));
writeFileSync(process.env.IVA_UPDATE_OUTCOME, JSON.stringify({ status: "updated", version: name, custom: "none", migrations: [], removed: [] }));
`;

void test("candidate and shipped v0.4.17/v0.4.9/v0.4.8 CLI unpack 0.5.0 before invoking its own update-finish", async (t) => {
  for (const tag of ["candidate", "v0.4.17", "v0.4.9", "v0.4.8"]) {
    const old = tag === "candidate" ? { cli, check } : await shipped(t, tag);
    for (const [from, to] of TRANSITIONS) {
      const root = workspace(t);
      const fx = releases(root, from, to);
      // The new half belongs to the target commit and must be unpacked by the old code.
      write(fx.source, "scripts/update-finish.ts", FINISH);
      git(fx.source, "add", "-A");
      git(fx.source, "commit", "-qm", "new updater entry");
      git(fx.source, "tag", "-f", `v${to}`);
      git(
        fx.source,
        "push",
        "-q",
        "--force",
        fx.remote,
        "main",
        `refs/tags/v${to}`,
      );
      const targetSha = git(fx.source, "rev-parse", "HEAD");
      const home = join(root, "home");
      mkdirSync(home);
      git(root, "clone", "-q", "--mirror", fx.remote, join(home, "repo"));
      write(home, ".env", "MODEL_PROVIDER=ollama\nAGENT_LANGUAGE=en\n");
      write(home, "data/sentinel", "user data\n");
      const store = createVersionStore(home);
      const before = versionName(from, fx.before);
      const dir = store.stage(before);
      write(
        dir,
        "package.json",
        JSON.stringify({ name: "iva", version: from, type: "module" }),
      );
      store.linkState(dir);
      store.complete(before);
      store.activate(before);
      store.settle(before);
      const target = await old.cli.resolveTarget(join(home, "repo"), fx.before);
      assert.equal(target.version, to, tag);
      assert.equal(target.sha, targetSha, tag);
      assert.equal(old.check.compareStableVersions(from, to), 1, tag);
      const command = old.cli.createVersionUpdateCommand(
        {
          ...createCliRuntime(dir),
          readEnv: () => ({ MODEL_PROVIDER: "ollama", AGENT_LANGUAGE: "en" }),
          childEnv: { ...process.env },
        },
        {
          restartServices: () =>
            assert.fail("old CLI must hand off before restart"),
        },
      );
      const exitBefore = process.exitCode;
      await command.run([]);
      assert.equal(process.exitCode, exitBefore, `${tag}: ${from} -> ${to}`);
      const name = versionName(to, targetSha);
      const unpacked = join(home, "versions", name);
      assert.deepEqual(
        JSON.parse(readFileSync(join(home, "handoff.json"), "utf8")),
        {
          home,
          name,
          version: to,
          cwd: unpacked,
          entry: pathToFileURL(join(unpacked, "scripts/update-finish.ts")).href,
        },
      );
      assert.equal(
        readFileSync(join(unpacked, "scripts/update-finish.ts"), "utf8"),
        FINISH,
      );
      assert.equal(store.currentName(), before);
      assert.equal(
        readFileSync(join(home, "data/sentinel"), "utf8"),
        "user data\n",
      );
    }
  }
});

void test("v0.4.8 Update resolver follows main across 0.5.0; installer and repair select the numeric release", async (t) => {
  const old = await shipped(t, "v0.4.8");
  for (const [from, to] of TRANSITIONS) {
    const root = workspace(t);
    const fx = releases(root, from, to);
    const clone = join(root, "clone");
    git(root, "clone", "-q", fx.remote, clone);
    assert.equal((await old.cli.resolveTarget(clone)).sha, fx.after);
    // Distinguish the old branch-tip rule from the new tagged-release rule.
    write(fx.source, "after-release", "main tip\n");
    git(fx.source, "add", "-A");
    git(fx.source, "commit", "-qm", "after release");
    git(fx.source, "push", "-q", fx.remote, "main");
    assert.equal(
      (await old.cli.resolveTarget(clone)).sha,
      git(fx.source, "rev-parse", "HEAD"),
    );
    for (const script of ["install.sh", "repair.sh"]) {
      git(clone, "fetch", "-q", "origin", "main");
      git(clone, "reset", "-q", "--hard", "FETCH_HEAD");
      const source = readFileSync(join(ROOT, script), "utf8");
      const fn = /^checkout_release\(\) \{[\s\S]*?^\}/mu.exec(source)?.[0];
      assert.ok(fn, script);
      execFileSync(
        "bash",
        ["-c", `${fn}\ncheckout_release "$1" "" HEAD`, script, clone],
        { env: { ...process.env, IVA_BETA: "" } },
      );
      assert.equal(
        git(clone, "rev-parse", "HEAD"),
        fx.after,
        `${script}: ${from} -> ${to}`,
      );
    }
  }
});

void test("version parser rejects paths while accepting 0.5.0 beta and rebuilt names", () => {
  for (const name of [
    "../0.5.0-0123456789ab",
    "0.5.0-0123456789ab/other",
    "0.5.0-beta.01-0123456789ab",
  ]) {
    assert.equal(parseVersionName(name), null);
  }
});
