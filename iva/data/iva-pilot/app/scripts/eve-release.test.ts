/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns registrations. */
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { hashEveTree } from "./lib/eve-tree-hash.ts";
import { eveReleaseVersion } from "./fixtures/eve-release-version.test-fixture.ts";
const ROOT = fileURLToPath(new URL("../", import.meta.url));
const VERSION = eveReleaseVersion(ROOT);
const TAG = "eve-" + VERSION;
const GIT = execFileSync("sh", ["-c", "command -v git"], {
  encoding: "utf8",
}).trim();
const CREATE = [
  "release",
  "create",
  TAG,
  "--repo",
  "smixs/iva-agent",
  "--verify-tag",
  "--latest=false",
  "--title",
  TAG,
  "--notes",
  "eve source archive for Iva.",
];
const TOOL = String.raw`#!/usr/bin/env node
import {spawnSync} from 'node:child_process';
import {appendFileSync, copyFileSync, existsSync, readdirSync, readFileSync, writeFileSync} from 'node:fs';
import {basename} from 'node:path';
const tool = basename(process.argv[1]), args = process.argv.slice(2);
const tag = ${JSON.stringify(TAG)};
const step = tool === 'git' ? args[0] === 'rev-parse' ? 'commit' : args[0] === 'tag' ? 'tag' : args[0] === 'push' ? 'push' : null : tool === 'gh' ? args[1] : tool === 'curl' ? 'download' : 'ci';
if (step) {
 appendFileSync(process.env.EVE_TEST_LOG, JSON.stringify({tool, step, args})+'\n');
 if (process.env.EVE_TEST_FAIL === step) process.exit(7);
}
if (tool === 'git') {
 if (args[0] === 'push') { if (args.join(' ') !== 'push origin refs/tags/' + tag) throw Error('unexpected push'); process.exit(0); }
 const result = spawnSync(process.env.EVE_TEST_GIT, args, {stdio: 'inherit'}); process.exit(result.status ?? 1);
}
if (tool === 'gh') {
 const state = process.env.EVE_TEST_STATE, asset = process.env.EVE_TEST_ASSET;
 if (args[1] === 'view') {
  if (!existsSync(state)) { console.error('release not found'); process.exit(1); }
  console.log(existsSync(asset) ? tag + '.tgz' : '');
 } else if (args[1] === 'create') {
  if (existsSync(state)) throw Error('release already exists');
  writeFileSync(state, 'release');
 } else if (args[1] === 'upload') {
  if (!existsSync(state) || existsSync(asset)) throw Error('invalid upload');
  copyFileSync(args[3], asset);
 } else if (args[1] === 'download') {
  copyFileSync(asset, args[args.indexOf('--output')+1]);
 } else throw Error('unexpected gh call');
}
if (tool === 'curl') {
 const dest = args[args.indexOf('--output')+1];
 if (process.env.EVE_TEST_FAIL === 'sri') writeFileSync(dest, 'wrong bytes');
 else copyFileSync(process.env.EVE_TEST_ASSET, dest);
}
if (tool === 'npm') {
 if (args[0] !== 'ci' || !process.cwd().endsWith('/ci')) throw Error('not clean ci');
 const cache = args[args.indexOf('--cache')+1];
 if (readdirSync(cache).length) throw Error('cache not empty');
 const lock = JSON.parse(readFileSync('package-lock.json','utf8'));
 if (!lock.packages['node_modules/eve'].resolved.startsWith('https://github.com/')) throw Error('not final URL');
}
`;
type Call = { tool: string; step: string; args: string[] };
async function fixture(t: TestContext) {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "eve-publish-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const dir of ["scripts/lib", "eve", "out/input/package", "tools"])
    mkdirSync(path.join(root, dir), { recursive: true });
  for (const file of ["scripts/eve-release.sh", "scripts/lib/eve-tree-hash.ts"])
    copyFileSync(path.join(ROOT, file), path.join(root, file));
  const json = (file: string, value: unknown) =>
    writeFileSync(path.join(root, file), JSON.stringify(value));
  json("package.json", { dependencies: { eve: "0.51.1" } });
  json("package-lock.json", {
    packages: {
      "": { dependencies: { eve: "0.51.1" } },
      "node_modules/eve": { version: "0.51.1" },
    },
  });
  writeFileSync(path.join(root, "eve/NOTICE"), "source");
  json("out/input/package/package.json", {
    name: "eve",
    version: VERSION,
  });
  json("out/input/package/BUILD.json", {
    packageVersion: VERSION,
    sourceTreeHash: await hashEveTree(path.join(root, "eve"), "source"),
    packedTreeHash: await hashEveTree(
      path.join(root, "out/input/package"),
      "packed",
    ),
  });
  const archive = path.join(root, "out", TAG + ".tgz");
  execFileSync("tar", [
    "-czf",
    archive,
    "-C",
    path.join(root, "out/input"),
    "package",
  ]);
  writeFileSync(path.join(root, ".gitignore"), "out/\ntools/\ncalls.log\n");
  const git = (...args: string[]) =>
    execFileSync(GIT, args, { cwd: root, encoding: "utf8" });
  git("init", "--quiet");
  const commit = () => {
    git("add", ".");
    git(
      "-c",
      "user.name=iva",
      "-c",
      "user.email=iva@example.invalid",
      "commit",
      "--quiet",
      "-m",
      "source",
    );
  };
  commit();
  execFileSync("bash", ["scripts/eve-release.sh", "out"], { cwd: root });
  commit();
  for (const tool of ["git", "gh", "curl", "npm"]) {
    writeFileSync(path.join(root, "tools", tool), TOOL);
    chmodSync(path.join(root, "tools", tool), 0o755);
  }
  // A missing archive must never invoke the preparation build.
  writeFileSync(
    path.join(root, "scripts/build-eve.sh"),
    "#!/bin/sh\necho BUILD_INVOKED >&2\nexit 9\n",
  );
  commit();
  const log = path.join(root, "calls.log"),
    state = path.join(root, "out/release"),
    asset = path.join(root, "out/published.tgz");
  const run = (failure = "none") => {
    writeFileSync(log, "");
    return spawnSync("bash", ["scripts/eve-release.sh", "--publish", "out"], {
      cwd: root,
      encoding: "utf8",
      env: {
        ...process.env,
        PATH: path.join(root, "tools") + ":" + (process.env.PATH ?? ""),
        EVE_TEST_LOG: log,
        EVE_TEST_GIT: GIT,
        EVE_TEST_FAIL: failure,
        EVE_TEST_STATE: state,
        EVE_TEST_ASSET: asset,
      },
    });
  };
  const calls = (): Call[] =>
    readFileSync(log, "utf8")
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line) as Call);
  const clean = () => assert.equal(git("status", "--porcelain"), "");
  return { root, archive, state, asset, git, commit, json, run, calls, clean };
}
function assertArguments(calls: Call[], archive: string) {
  for (const call of calls) {
    if (call.step === "create") assert.deepEqual(call.args, CREATE);
    if (call.step === "upload")
      assert.deepEqual(call.args, [
        "release",
        "upload",
        TAG,
        archive,
        "--repo",
        "smixs/iva-agent",
      ]);
    if (call.step === "push")
      assert.deepEqual(call.args, ["push", "origin", "refs/tags/" + TAG]);
  }
}
const STEPS = [
  "commit",
  "tag",
  "push",
  "view",
  "create",
  "upload",
  "download",
  "ci",
];
for (const failure of ["none", ...STEPS, "sri"]) {
  test("publication runs in order and stops at " + failure, async (t) => {
    const f = await fixture(t),
      result = f.run(failure),
      output = result.stdout + result.stderr;
    if (failure === "none") assert.equal(result.status, 0, output);
    else assert.notEqual(result.status, 0, output);
    const expected =
      failure === "none"
        ? STEPS
        : STEPS.slice(
            0,
            STEPS.indexOf(failure === "sri" ? "download" : failure) + 1,
          );
    assert.deepEqual(
      f.calls().map((call) => call.step),
      expected,
    );
    assertArguments(f.calls(), f.archive);
    assert.equal(
      result.stdout.includes("Можно пушить beta"),
      failure === "none",
    );
    f.clean();
  });
}
for (const failure of ["upload", "download", "ci"]) {
  test("publication retries after " + failure, async (t) => {
    const f = await fixture(t),
      first = f.run(failure);
    assert.notEqual(first.status, 0, first.stdout + first.stderr);
    const retry = f.run();
    assert.equal(retry.status, 0, retry.stdout + retry.stderr);
    const calls = f.calls();
    assert.ok(!calls.some((call) => call.step === "create"));
    assert.equal(
      calls.some((call) => call.step === "upload"),
      failure === "upload",
    );
    assertArguments(calls, f.archive);
    assert.ok(retry.stdout.includes("Можно пушить beta"));
    f.clean();
  });
}
for (const asset of ["absent", "matching", "different"]) {
  test("existing release with " + asset + " asset", async (t) => {
    const f = await fixture(t);
    writeFileSync(f.state, "release");
    if (asset === "matching") copyFileSync(f.archive, f.asset);
    if (asset === "different")
      writeFileSync(f.asset, "immutable different bytes");
    const result = f.run(),
      calls = f.calls(),
      output = result.stdout + result.stderr;
    assert.ok(!calls.some((call) => call.step === "create"));
    if (asset === "different") {
      assert.notEqual(result.status, 0, output);
      assert.match(output, /опубликованный архив неизменяем.*новый N/iu);
      assert.ok(
        !calls.some((call) => call.step === "upload" || call.step === "ci"),
      );
      assert.equal(readFileSync(f.asset, "utf8"), "immutable different bytes");
    } else {
      assert.equal(result.status, 0, output);
      assert.equal(
        calls.some((call) => call.step === "upload"),
        asset === "absent",
      );
      assert.deepEqual(readFileSync(f.asset), readFileSync(f.archive));
    }
    assertArguments(calls, f.archive);
    f.clean();
  });
}
for (const condition of [
  "eve.lock.json",
  "package-lock.json",
  "missing archive",
  "different tag",
]) {
  test("publication rejects precondition: " + condition, async (t) => {
    const f = await fixture(t);
    if (condition === "eve.lock.json") {
      const ref = JSON.parse(
        readFileSync(path.join(f.root, condition), "utf8"),
      ) as Record<string, string>;
      ref.sourceTreeHash = "0".repeat(64);
      f.json(condition, ref);
      f.commit();
    }
    if (condition === "package-lock.json") {
      const lock = JSON.parse(
        readFileSync(path.join(f.root, condition), "utf8"),
      ) as { packages: Record<string, { integrity: string }> };
      lock.packages["node_modules/eve"].integrity = "sha512-wrong";
      f.json(condition, lock);
      f.commit();
    }
    if (condition === "missing archive") rmSync(f.archive);
    if (condition === "different tag") f.git("tag", TAG, "HEAD~1");
    const result = f.run(),
      output = result.stdout + result.stderr;
    assert.notEqual(result.status, 0, output);
    assert.ok(
      !f.calls().some((call) => call.step === "push" || call.tool === "gh"),
    );
    assert.ok(!output.includes("BUILD_INVOKED"));
    assert.match(
      output,
      condition === "missing archive"
        ? /Prepare and commit the archive/
        : condition === "different tag"
          ? /Existing tag belongs to another commit/
          : /AssertionError/,
    );
    f.clean();
  });
}
