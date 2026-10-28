/* eslint-disable @typescript-eslint/no-floating-promises -- Node owns registrations. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  chmod,
  cp,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { hashEveTree, listEveTreeFiles } from "./lib/eve-tree-hash.ts";

const OUTPUTS = [
  "node_modules",
  "dist",
  ".generated",
  ".turbo",
  ...["eve", "eve-catalog"].flatMap((pkg) =>
    ["node_modules", "dist", ".generated", ".turbo"].map(
      (dir) => `packages/${pkg}/${dir}`,
    ),
  ),
  "packages/eve/docs",
];
const COPIES = ["BUILD.json", "LICENSE", "NOTICE"].map(
  (file) => `packages/eve/${file}`,
);
async function fixture(t: test.TestContext) {
  const root = await mkdtemp(path.join(tmpdir(), "eve-hash-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}
test("source hash excludes exactly the generated build paths", async (t) => {
  const root = await fixture(t);
  await writeFile(path.join(root, "NOTICE"), "authored notice");
  const before = await hashEveTree(root, "source");
  for (const dir of OUTPUTS) {
    await mkdir(path.join(root, dir), { recursive: true });
    await writeFile(path.join(root, dir, "generated"), "build output");
  }
  for (const file of COPIES) await writeFile(path.join(root, file), "copy");
  assert.deepEqual(await listEveTreeFiles(root, "source"), ["NOTICE"]);
  assert.equal(await hashEveTree(root, "source"), before);
  for (const name of ["dist", "node_modules", ".generated", ".turbo", "docs"]) {
    const dir = path.join(root, "packages/eve/src", name);
    await mkdir(dir, { recursive: true });
    await writeFile(path.join(dir, "x.ts"), "authored source");
    assert.ok(
      (await listEveTreeFiles(root, "source")).includes(
        `packages/eve/src/${name}/x.ts`,
      ),
    );
  }
  assert.notEqual(await hashEveTree(root, "source"), before);
});
test("clean clones and unpacked archives hash identically under umask 022, 002 and 077", async (t) => {
  const root = await fixture(t),
    source = path.join(root, "source");
  await mkdir(source);
  await writeFile(path.join(source, "plain.ts"), "source");
  await writeFile(path.join(source, "executable.sh"), "#!/bin/sh\n");
  await chmod(path.join(source, "executable.sh"), 0o755);
  const git = (...args: string[]) => execFileSync("git", args, { cwd: source });
  git("init", "--quiet");
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
  git("archive", "--format=tar", "-o", path.join(root, "source.tar"), "HEAD");
  const hashes: string[][] = [];
  for (const mask of ["022", "002", "077"]) {
    const clone = path.join(root, `clone-${mask}`),
      packed = path.join(root, `packed-${mask}`);
    execFileSync("sh", [
      "-c",
      'umask "$1"; git clone --quiet "$2" "$3"; mkdir "$4"; tar --no-same-permissions -xf "$5" -C "$4"',
      "sh",
      mask,
      source,
      clone,
      packed,
      path.join(root, "source.tar"),
    ]);
    await rm(path.join(clone, ".git"), { recursive: true });
    hashes.push([
      await hashEveTree(clone, "source"),
      await hashEveTree(packed, "packed"),
    ]);
  }
  assert.deepEqual(hashes[1], hashes[0]);
  assert.deepEqual(hashes[2], hashes[0]);
  const copy = path.join(root, "changed");
  await cp(path.join(root, "packed-022"), copy, { recursive: true });
  await chmod(path.join(copy, "plain.ts"), 0o755);
  assert.notEqual(await hashEveTree(copy, "packed"), hashes[0]?.[1]);
});

test("vendor build stamps are identical in different package directories", async (t) => {
  const root = await fixture(t);
  const stamps: string[] = [];
  for (const name of ["one", "another/location"]) {
    const pkg = path.join(root, name);
    await mkdir(pkg, { recursive: true });
    await writeFile(path.join(pkg, "input.mjs"), "same build input");
    execFileSync(process.execPath, [
      "--input-type=module",
      "-e",
      `
      import {runVendor} from ${JSON.stringify(new URL("../eve/packages/eve/scripts/vendor-compiled/_shared.mjs", import.meta.url).href)};
      import {join} from 'node:path';
      const packageRoot = process.argv[1];
      await runVendor({packageRoot, compiledRoot: join(packageRoot, 'compiled'), modules: [], scriptFiles: [join(packageRoot, 'input.mjs')]});
    `,
      pkg,
    ]);
    stamps.push(
      await readFile(path.join(pkg, "compiled/.vendor-stamp.json"), "utf8"),
    );
  }
  assert.equal(stamps[1], stamps[0]);
});
