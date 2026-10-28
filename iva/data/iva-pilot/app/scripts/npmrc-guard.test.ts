import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";
import fc from "fast-check";

const ROOT = resolve(import.meta.dirname, "..");
const SEED = Number(process.env.FC_SEED ?? 101512);
type Manifest = {
  dependencies?: Record<string, string>;
  optionalDependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
};

function parseNpmrc(text: string): Map<string, string> {
  const settings = new Map<string, string>();
  for (const line of text.split(/\r?\n/u)) {
    const assignment = line.split(/[;#]/u)[0].trim();
    const match = /^([^=\s]+)\s*=\s*(.*?)\s*$/u.exec(assignment);
    if (match) settings.set(match[1], match[2]);
  }
  return settings;
}

function assertDependencySettings(manifest: Manifest, npmrc: string): void {
  const settings = parseNpmrc(npmrc);
  const dependencies = [
    ...Object.values(manifest.dependencies ?? {}),
    ...Object.values(manifest.optionalDependencies ?? {}),
  ];
  for (const specifier of dependencies) {
    let key: string;
    if (/^https?:\/\//u.test(specifier)) key = "allow-remote";
    else if (
      /^(?:git\+|github:)/u.test(specifier) ||
      /^[\w.-]+\/[\w.-]+(?:#.*)?$/u.test(specifier)
    )
      key = "allow-git";
    else continue;
    assert.ok(
      settings.get(key) === "root" || settings.get(key) === "all",
      `${specifier} requires ${key}=root|all in the root .npmrc for npm 12`,
    );
  }
}

await test(`npmrc parsing respects comments and the last assignment (seed ${SEED})`, () => {
  fc.assert(
    fc.property(
      fc.constantFrom("allow-remote", "allow-git"),
      fc.constantFrom("root", "all", "none"),
      fc.constantFrom(";", "#"),
      fc.constantFrom("", " ", "\t"),
      fc.constantFrom("\n", "\r\n"),
      (key, value, comment, space, newline) => {
        const text = [
          `${space}${key}=none`,
          `${space}${comment} ${key}=all`,
          `${space}${key}${space}=${space}${value}${space}${comment} explanation`,
          `${space}${comment} ${key}=none`,
          "",
        ].join(newline);
        assert.deepEqual([...parseNpmrc(text)], [[key, value]]);
      },
    ),
    { seed: SEED },
  );
});

for (const [key, specifiers] of [
  [
    "allow-remote",
    ["http://example.invalid/pkg.tgz", "https://example.invalid/pkg.tgz"],
  ],
  [
    "allow-git",
    [
      "git+https://example.invalid/owner/repo.git",
      "github:owner/repo",
      "owner/repo#main",
    ],
  ],
] as const) {
  await test(`${key} is required for dependencies and optionalDependencies (seed ${SEED})`, () => {
    fc.assert(
      fc.property(
        fc.constantFrom("dependencies", "optionalDependencies"),
        fc.constantFrom(...specifiers),
        fc.constantFrom("root", "all"),
        (section, specifier, permission) => {
          const manifest = { [section]: { example: specifier } };
          assert.doesNotThrow(() =>
            assertDependencySettings(manifest, `${key}=${permission}`),
          );
          for (const npmrc of [
            "",
            `${key}=none`,
            `${key}=true`,
            `# ${key}=root`,
            `; ${key}=all`,
          ]) {
            assert.throws(() => assertDependencySettings(manifest, npmrc), {
              message: new RegExp(`${key}=root\\|all`, "u"),
            });
          }
        },
      ),
      { seed: SEED },
    );
  });
}

await test("registry, scoped and local dependencies need neither permission", () => {
  assertDependencySettings({}, "");
  assertDependencySettings(
    {
      dependencies: {
        registry: "^1.0.0",
        scoped: "npm:@scope/pkg@1.0.0",
        local: "file:packages/example",
      },
      optionalDependencies: { workspace: "workspace:*" },
      devDependencies: {
        remote: "https://example.invalid/dev.tgz",
        git: "owner/dev",
      },
    },
    "",
  );
});

await test("remote and git dependencies require independent permissions", () => {
  const manifest = {
    dependencies: { remote: "https://example.invalid/pkg.tgz" },
    optionalDependencies: { git: "owner/repo" },
  };
  assert.throws(
    () => assertDependencySettings(manifest, "allow-remote=all"),
    /allow-git/u,
  );
  assert.throws(
    () => assertDependencySettings(manifest, "allow-git=all"),
    /allow-remote/u,
  );
  assertDependencySettings(manifest, "allow-remote=root\nallow-git=root");
});

await test("root npmrc permits the manifest's remote and git dependencies under npm 12", () => {
  const manifest = JSON.parse(
    readFileSync(join(ROOT, "package.json"), "utf8"),
  ) as Manifest;
  assertDependencySettings(
    manifest,
    readFileSync(join(ROOT, ".npmrc"), "utf8"),
  );
});

await test("root npmrc is tracked and included in git archive", (t) => {
  if (!existsSync(join(ROOT, ".git"))) {
    t.skip(
      "Git metadata is absent; mini-run omits .git, so tracking and export-ignore cannot be checked here.",
    );
    return;
  }
  const git = (...args: string[]) =>
    execFileSync("git", args, { cwd: ROOT, encoding: "utf8" }).trim();
  assert.equal(git("ls-files", "--error-unmatch", "--", ".npmrc"), ".npmrc");
  assert.notEqual(
    git("check-attr", "export-ignore", "--", ".npmrc"),
    ".npmrc: export-ignore: set",
  );
});
