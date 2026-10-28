import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import {
  acquireUpdateLock,
  createVersionStore,
  updateRunning,
} from "./version-store.ts";

void test("an ownerless stale observer preserves a successor paused between mkdir and owner publication", async (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-ownerless-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const path = join(data, "update.lock");
  fs.mkdirSync(path);
  const old = new Date(Date.now() - 2 * 3600_000);
  fs.utimesSync(path, old, old);
  const created = join(data, "created");
  const resume = join(data, "resume");
  const ready = join(data, "ready.json");
  const moduleUrl = pathToFileURL(
    join(import.meta.dirname, "version-store.ts"),
  ).href;
  const waitFor = (file: string) => {
    const deadline = Date.now() + 10_000;
    while (!fs.existsSync(file)) {
      assert.ok(Date.now() < deadline, `reached ${file}`);
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
    }
  };
  let child: ChildProcess | undefined;
  let exited: Promise<void> | undefined;
  const rename = fs.renameSync;
  let scheduled = false;
  t.mock.method(
    fs,
    "renameSync",
    (...args: Parameters<typeof fs.renameSync>) => {
      if (String(args[0]) === path && !scheduled) {
        scheduled = true;
        child = spawn(
          process.execPath,
          [
            "--input-type=module",
            "--eval",
            `
        import fs from "node:fs";
        import { syncBuiltinESMExports } from "node:module";
        const mkdir = fs.mkdirSync;
        fs.mkdirSync = (...args) => {
          const result = mkdir(...args);
          if (String(args[0]) === ${JSON.stringify(path)} && args[1] === undefined) {
            fs.writeFileSync(${JSON.stringify(created)}, "");
            const deadline = Date.now() + 10000;
            while (!fs.existsSync(${JSON.stringify(resume)})) {
              if (Date.now() > deadline) throw new Error("observer timeout");
              Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
            }
          }
          return result;
        };
        syncBuiltinESMExports();
        const { acquireUpdateLock } = await import(${JSON.stringify(moduleUrl)});
        const lock = acquireUpdateLock(${JSON.stringify(data)});
        fs.writeFileSync(${JSON.stringify(ready)}, JSON.stringify({ acquired: lock !== null, pid: process.pid }));
        setInterval(() => {}, 1000);
      `,
          ],
          { stdio: "ignore" },
        );
        exited = new Promise((resolve) => child!.once("exit", () => resolve()));
        waitFor(created);
      }
      return rename(...args);
    },
  );
  syncBuiltinESMExports();
  try {
    const observer = acquireUpdateLock(data);
    fs.writeFileSync(resume, "");
    waitFor(ready);
    const successor = JSON.parse(fs.readFileSync(ready, "utf8")) as {
      acquired: boolean;
      pid: number;
    };
    assert.equal(successor.acquired, true);
    process.kill(successor.pid, 0);
    assert.equal(observer, null, "only the successor owns the update lock");
    assert.equal(
      (
        JSON.parse(fs.readFileSync(join(path, "owner.json"), "utf8")) as {
          pid: number;
        }
      ).pid,
      successor.pid,
    );
    assert.deepEqual(
      fs
        .readdirSync(data)
        .filter((name) => name.startsWith("update.lock.stale-")),
      [],
    );
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
    child?.kill("SIGKILL");
    await exited;
  }
});

void test("an ownerless update lock becomes reclaimable after the short publication window", (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-young-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const path = join(data, "update.lock");
  fs.mkdirSync(path);
  assert.equal(acquireUpdateLock(data), null);
  const old = new Date(Date.now() - 10_000);
  fs.utimesSync(path, old, old);
  const lock = acquireUpdateLock(data);
  assert.ok(
    lock,
    "a crash before owner publication delays takeover for seconds",
  );
  lock.release();
});

void test("a fresh ownerless directory stays at its path without a takeover rename", (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-unmoved-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const path = join(data, "update.lock");
  fs.mkdirSync(path);
  const now = Date.parse("2026-10-13T12:00:00Z");
  const young = new Date(now - 1_000);
  fs.utimesSync(path, young, young);
  t.mock.method(Date, "now", () => now);
  const rename = fs.renameSync;
  const moved: string[] = [];
  t.mock.method(
    fs,
    "renameSync",
    (...args: Parameters<typeof fs.renameSync>) => {
      if (String(args[0]) === path) moved.push(String(args[1]));
      return rename(...args);
    },
  );
  syncBuiltinESMExports();
  try {
    assert.equal(acquireUpdateLock(data), null);
    assert.deepEqual(
      moved,
      [],
      "the pending publisher keeps its directory at the original path",
    );
    assert.deepEqual(fs.readdirSync(data), ["update.lock"]);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

for (const kind of [
  "unreadable-owner",
  "owner-directory",
  "lock-file",
] as const)
  void test(`an update lock with ${kind} is occupied and acquisition returns null`, (t) => {
    const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-hostile-"));
    t.after(() => fs.rmSync(data, { recursive: true, force: true }));
    const path = join(data, "update.lock");
    if (kind === "lock-file") fs.writeFileSync(path, "foreign");
    else fs.mkdirSync(path);
    const owner = join(path, "owner.json");
    if (kind === "owner-directory") fs.mkdirSync(owner);
    if (kind === "unreadable-owner") {
      fs.writeFileSync(owner, "private");
      const read = fs.readFileSync;
      t.mock.method(
        fs,
        "readFileSync",
        (...args: Parameters<typeof fs.readFileSync>) => {
          if (String(args[0]) === owner)
            throw Object.assign(new Error("permission denied"), {
              code: "EACCES",
            });
          return read(...args);
        },
      );
      syncBuiltinESMExports();
    }
    try {
      assert.equal(acquireUpdateLock(data), null);
    } finally {
      t.mock.restoreAll();
      syncBuiltinESMExports();
    }
    assert.equal(fs.existsSync(path), true);
    assert.deepEqual(fs.readdirSync(data), ["update.lock"]);
  });

void test("the owner snapshot precedes liveness inspection when a successor takes over during that inspection", (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-inspection-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const path = join(data, "update.lock");
  fs.mkdirSync(path);
  fs.writeFileSync(
    join(path, "owner.json"),
    JSON.stringify({ pid: 2147483647 }),
  );
  const kill = process.kill.bind(process);
  let scheduled = false;
  let successor: ReturnType<typeof acquireUpdateLock> | undefined;
  t.mock.method(process, "kill", (...args: Parameters<typeof process.kill>) => {
    if (args[0] === 2147483647 && !scheduled) {
      scheduled = true;
      successor = acquireUpdateLock(data);
      assert.ok(successor);
    }
    return kill(...args);
  });
  try {
    assert.equal(acquireUpdateLock(data), null);
    assert.ok(scheduled);
    assert.equal(updateRunning(data), true);
    assert.equal(
      (
        JSON.parse(fs.readFileSync(join(path, "owner.json"), "utf8")) as {
          pid: number;
        }
      ).pid,
      process.pid,
    );
  } finally {
    t.mock.restoreAll();
    successor?.release();
  }
});

for (const third of [false, true])
  void test(
    third
      ? "a third updater occupying the restore path preserves the displaced owner's directory and reports it"
      : "a stale observer yields to the live updater that acquired before its takeover",
    async (t) => {
      const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-race-"));
      t.after(() => fs.rmSync(data, { recursive: true, force: true }));
      const path = join(data, "update.lock");
      const ready = join(data, "ready.json");
      fs.mkdirSync(path);
      fs.writeFileSync(
        join(path, "owner.json"),
        JSON.stringify({ pid: 2147483647 }),
      );
      const remove = fs.rmSync;
      const rename = fs.renameSync;
      let child: ChildProcess | undefined;
      let exited: Promise<void> | undefined;
      let scheduled = false;
      let thirdChild: ChildProcess | undefined;
      let thirdExited: Promise<void> | undefined;
      const thirdReady = join(data, "third-ready.json");
      const messages: string[] = [];
      if (third)
        t.mock.method(console, "error", (message: string) =>
          messages.push(message),
        );
      const successor = () => {
        if (scheduled) return;
        scheduled = true;
        child = spawn(
          process.execPath,
          [
            join(import.meta.dirname, "../fixtures/update-lock-holder.ts"),
            data,
            ready,
          ],
          {
            stdio: "ignore",
          },
        );
        exited = new Promise((resolve) => child!.once("exit", () => resolve()));
        const deadline = Date.now() + 10_000;
        while (!fs.existsSync(ready) && Date.now() < deadline)
          Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
        assert.ok(
          fs.existsSync(ready),
          "the successor completed its acquisition",
        );
      };
      // Audit schedule: pause the observer before its destructive filesystem step.
      fs.rmSync = (...args) => {
        if (String(args[0]) === path) successor();
        return remove(...args);
      };
      fs.renameSync = (...args) => {
        if (String(args[0]) === path) successor();
        const result = rename(...args);
        if (third && String(args[0]) === path) {
          thirdChild = spawn(
            process.execPath,
            [
              join(import.meta.dirname, "../fixtures/update-lock-holder.ts"),
              data,
              thirdReady,
            ],
            { stdio: "ignore" },
          );
          thirdExited = new Promise((resolve) =>
            thirdChild!.once("exit", () => resolve()),
          );
          const deadline = Date.now() + 10_000;
          while (!fs.existsSync(thirdReady) && Date.now() < deadline)
            Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
          assert.ok(fs.existsSync(thirdReady));
          const thirdOwner = JSON.parse(
            fs.readFileSync(thirdReady, "utf8"),
          ) as { pid: number; acquired: boolean };
          assert.equal(
            thirdOwner.acquired,
            true,
            "the third updater occupies the temporarily empty path",
          );
          process.kill(thirdOwner.pid, 0);
        }
        return result;
      };
      syncBuiltinESMExports();
      try {
        const observer = acquireUpdateLock(data);
        assert.ok(scheduled, "the audit window was reached");
        const held = JSON.parse(fs.readFileSync(ready, "utf8")) as {
          pid: number;
          acquired: boolean;
        };
        assert.equal(held.acquired, true);
        process.kill(held.pid, 0);
        assert.equal(
          observer,
          null,
          "a retry preserves the live successor's lock",
        );
        assert.equal(updateRunning(data), true);
        const leftovers = fs
          .readdirSync(data)
          .filter((name) => name.startsWith("update.lock.stale-"));
        if (third) {
          assert.equal(
            (
              JSON.parse(fs.readFileSync(join(path, "owner.json"), "utf8")) as {
                pid: number;
              }
            ).pid,
            thirdChild?.pid,
          );
          assert.equal(leftovers.length, 1);
          assert.equal(
            (
              JSON.parse(
                fs.readFileSync(join(data, leftovers[0], "owner.json"), "utf8"),
              ) as { pid: number }
            ).pid,
            held.pid,
          );
          assert.ok(
            messages.some(
              (message) =>
                message.includes(join(data, leftovers[0])) &&
                message.includes("owner changed"),
            ),
          );
        } else {
          assert.equal(
            (
              JSON.parse(fs.readFileSync(join(path, "owner.json"), "utf8")) as {
                pid: number;
              }
            ).pid,
            held.pid,
          );
          assert.deepEqual(leftovers, []);
        }
      } finally {
        fs.rmSync = remove;
        fs.renameSync = rename;
        syncBuiltinESMExports();
        thirdChild?.kill("SIGKILL");
        child?.kill("SIGKILL");
        await thirdExited;
        await exited;
      }
    },
  );

void test("takeover compares the complete owner contents and restores a changed owner", (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-owner-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const path = join(data, "update.lock");
  fs.mkdirSync(path);
  fs.writeFileSync(
    join(path, "owner.json"),
    JSON.stringify({ pid: 2147483647 }),
  );
  const changed = JSON.stringify({
    pid: 2147483647,
    startedAt: "2026-10-13T12:00:00.000Z",
  });
  const rename = fs.renameSync;
  t.mock.method(
    fs,
    "renameSync",
    (...args: Parameters<typeof fs.renameSync>) => {
      if (String(args[0]) === path)
        fs.writeFileSync(join(path, "owner.json"), changed);
      return rename(...args);
    },
  );
  syncBuiltinESMExports();
  try {
    assert.equal(acquireUpdateLock(data), null);
    assert.equal(fs.readFileSync(join(path, "owner.json"), "utf8"), changed);
    assert.deepEqual(fs.readdirSync(data), ["update.lock"]);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

void test("an abandoned update lock that vanished before rename yields to the other contender", (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-vanished-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const path = join(data, "update.lock");
  fs.mkdirSync(path);
  fs.writeFileSync(
    join(path, "owner.json"),
    JSON.stringify({ pid: 2147483647 }),
  );
  const rename = fs.renameSync;
  t.mock.method(
    fs,
    "renameSync",
    (...args: Parameters<typeof fs.renameSync>) => {
      if (String(args[0]) === path) {
        fs.rmSync(path, { recursive: true });
        throw Object.assign(new Error("another contender moved it"), {
          code: "ENOENT",
        });
      }
      return rename(...args);
    },
  );
  syncBuiltinESMExports();
  try {
    assert.equal(acquireUpdateLock(data), null);
    assert.deepEqual(fs.readdirSync(data), []);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

void test("a contender claiming after the abandoned directory was discarded keeps its lock", (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-claim-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const path = join(data, "update.lock");
  fs.mkdirSync(path);
  fs.writeFileSync(
    join(path, "owner.json"),
    JSON.stringify({ pid: 2147483647 }),
  );
  const remove = fs.rmSync;
  let successor: ReturnType<typeof acquireUpdateLock> | undefined;
  t.mock.method(fs, "rmSync", (...args: Parameters<typeof fs.rmSync>) => {
    const result = remove(...args);
    if (String(args[0]).startsWith(`${path}.stale-`)) {
      successor = acquireUpdateLock(data);
      assert.ok(successor);
    }
    return result;
  });
  syncBuiltinESMExports();
  try {
    assert.equal(acquireUpdateLock(data), null);
    assert.equal(updateRunning(data), true);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
    successor?.release();
  }
});

void test("version sweep removes only forgotten takeover directories older than a day", (t) => {
  const home = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-sweep-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  fs.writeFileSync(join(home, ".env"), "ASSISTANT_DATA_DIR=state\n");
  const store = createVersionStore(home);
  const data = store.layout.data;
  fs.mkdirSync(data, { recursive: true });
  const now = Date.UTC(2026, 9, 13, 12);
  const day = 24 * 60 * 60 * 1000;
  t.mock.method(Date, "now", () => now);
  const directory = (name: string, age: number, owner?: string) => {
    const path = join(data, name);
    fs.mkdirSync(path);
    if (owner !== undefined) fs.writeFileSync(join(path, "owner.json"), owner);
    const when = new Date(now - age);
    fs.utimesSync(path, when, when);
    return path;
  };
  const dead = JSON.stringify({ pid: 2147483647 });
  const aged = directory("update.lock.stale-aged", day + 1, dead);
  const orphan = directory("update.lock.stale-orphan", 2 * day);
  const boundary = directory("update.lock.stale-boundary", day, dead);
  const fresh = directory("update.lock.stale-fresh", day - 1, dead);
  const unrelated = directory("other.stale-aged", 2 * day, dead);
  const mine = acquireUpdateLock(data);
  assert.ok(mine);
  const live = join(data, "update.lock.stale-live");
  fs.renameSync(mine.path, live);
  fs.utimesSync(live, new Date(now - 2 * day), new Date(now - 2 * day));
  const current = acquireUpdateLock(data);
  assert.ok(current);
  const bytes = fs.readFileSync(join(current.path, "owner.json"), "utf8");
  const target = join(home, "sentinel");
  fs.mkdirSync(target);
  fs.writeFileSync(join(target, "owner.json"), "keep");
  const link = join(data, "update.lock.stale-link");
  fs.symlinkSync(target, link);
  const file = join(data, "update.lock.stale-file");
  fs.writeFileSync(file, "keep");

  assert.deepEqual(store.sweep().sort(), [
    "update.lock.stale-aged",
    "update.lock.stale-orphan",
  ]);
  assert.equal(fs.existsSync(aged), false);
  assert.equal(fs.existsSync(orphan), false);
  for (const path of [boundary, fresh, unrelated, live, link, file])
    assert.equal(fs.existsSync(path), true, path);
  assert.equal(fs.readFileSync(join(target, "owner.json"), "utf8"), "keep");
  assert.equal(
    fs.readFileSync(join(current.path, "owner.json"), "utf8"),
    bytes,
  );
});

void test("handoff retains owner.json compatibility and the parent release preserves its child", async (t) => {
  const data = fs.mkdtempSync(join(tmpdir(), "iva-update-lock-handoff-"));
  t.after(() => fs.rmSync(data, { recursive: true, force: true }));
  const parent = acquireUpdateLock(data);
  assert.ok(parent);
  const ready = join(data, "ready.json");
  const child = spawn(
    process.execPath,
    [
      join(import.meta.dirname, "../fixtures/update-lock-holder.ts"),
      data,
      ready,
      "adopt",
    ],
    { stdio: "ignore" },
  );
  const exited = new Promise<void>((resolve) =>
    child.once("exit", () => resolve()),
  );
  try {
    const deadline = Date.now() + 10_000;
    while (!fs.existsSync(ready) && Date.now() < deadline)
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
    assert.ok(fs.existsSync(ready));
    const bytes = fs.readFileSync(join(parent.path, "owner.json"), "utf8");
    const owner = JSON.parse(bytes) as { pid: number };
    assert.equal(owner.pid, child.pid);
    assert.deepEqual(Object.keys(owner).sort(), [
      "command",
      "pid",
      "startedAt",
    ]);
    parent.release();
    assert.equal(updateRunning(data), true);
    assert.equal(
      fs.readFileSync(join(parent.path, "owner.json"), "utf8"),
      bytes,
    );
  } finally {
    child.kill("SIGKILL");
    await exited;
  }
});
