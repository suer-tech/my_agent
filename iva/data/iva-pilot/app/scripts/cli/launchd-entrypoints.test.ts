import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { plantCliTree } from "../fixtures/cli-tree.ts";

void test(
  "macOS CLI writes, activates, restarts and removes only its launchd units",
  { skip: process.platform !== "darwin" },
  async (t) => {
    const dir = mkdtempSync(join(tmpdir(), "iva-launchd-cli-"));
    t.after(() => rmSync(dir, { force: true, recursive: true }));
    const project = join(dir, "iva");
    const home = join(dir, "home");
    const bin = join(dir, "bin");
    const state = join(dir, "state.json");
    const calls = join(dir, "calls.jsonl");
    mkdirSync(home);
    mkdirSync(bin);
    await plantCliTree(join(import.meta.dirname, "../.."), project);
    writeFileSync(
      join(project, ".env"),
      `IVA_PORT=8723\nASSISTANT_TIMEZONE=UTC\nASSISTANT_BEARER=${"a".repeat(43)}\n`,
    );
    const runner = join(dir, "fake.ts");
    writeFileSync(
      runner,
      `import {readFileSync,writeFileSync,existsSync,appendFileSync} from 'node:fs';
import {basename} from 'node:path';
const args=process.argv.slice(2),action=args[0],target=args[1];
appendFileSync(process.env.IVA_CALLS,JSON.stringify(args)+'\\n');
const calendar=readFileSync(${JSON.stringify(join(import.meta.dirname, "../fixtures/launchctl-print-calendar.txt"))},'utf8');
const path=process.env.IVA_STATE;const states=existsSync(path)?JSON.parse(readFileSync(path,'utf8')):{};
const label=target?.split('/').at(-1);const item=states[label];
if(item?.until && Date.now()>=item.until){item.loaded=false;item.running=false;delete item.until;writeFileSync(path,JSON.stringify(states));}
if(action==='print'&&new RegExp('^gui/[0-9]+$').test(target))process.exit(0);
if(action==='print-disabled'){console.log('disabled services = {\\n'+Object.entries(states).map(([l,s])=>'"'+l+'" => '+(s.disabled?'disabled':'enabled')).join('\\n')+'\\n}');process.exit(0);}
if(action==='print'){if(!item?.loaded)process.exit(113);const state=item.until?'SIGTERMed':item.running?'running':'waiting';console.log(item.calendar?calendar.replaceAll('app.iva-probe.repair-calendar',label).replace(/\\tstate = running/, '\\tstate = '+state).replace(/\\tpid = [0-9]+/,item.running?'\\tpid = 123':''):target+' = {\\n\\tstate = '+state+'\\n'+(item.running?'\\tpid = 123\\n':'')+'\\tlast exit code = 0\\n}');process.exit(0);}
if(action==='bootstrap'){const name=basename(args[2],'.plist');if(states[name]?.loaded)process.exit(5);const xml=readFileSync(args[2],'utf8');states[name]={...states[name],loaded:true,running:xml.includes('<key>KeepAlive</key>'),calendar:xml.includes('<key>StartCalendarInterval</key>')};}
if(action==='bootout'){if(!item?.loaded)process.exit(3);item.until=Date.now()+150;}
if(action==='enable'||action==='disable'){states[label]={...item,disabled:action==='disable'};}
if(action==='kickstart'){const name=args.at(-1).split('/').at(-1);states[name].running=true;}
if(action==='kill'){if(!item?.loaded)process.exit(113);item.running=false;}
writeFileSync(path,JSON.stringify(states));
`,
    );
    writeFileSync(
      join(bin, "launchctl"),
      `#!/bin/sh\nexec '${process.execPath}' '${runner}' "$@"\n`,
      { mode: 0o755 },
    );
    const run = (...args: string[]) =>
      spawnSync(process.execPath, [join(project, "bin/iva.mjs"), ...args], {
        encoding: "utf8",
        timeout: 15_000,
        cwd: project,
        env: {
          ...process.env,
          HOME: home,
          PATH: `${bin}:/usr/bin:/bin`,
          NO_COLOR: "1",
          IVA_STATE: state,
          IVA_CALLS: calls,
        },
      });
    const install = run("_install-units");
    assert.equal(install.status, 0, install.stderr);
    assert.match(install.stdout, /задания Ивы written: 7/u);
    const units = join(home, "Library/LaunchAgents");
    const plists = readdirSync(units);
    assert.equal(plists.length, 5);
    for (const plist of plists)
      assert.equal(
        spawnSync("/usr/bin/plutil", ["-lint", join(units, plist)]).status,
        0,
      );
    const activate = run("_activate-units");
    assert.equal(activate.status, 0, activate.stderr);
    const status = run("status");
    assert.equal(status.status, 0, status.stderr);
    assert.match(status.stdout, /iva.service: running/u);
    assert.match(status.stdout, /iva-brain.timer: loaded/u);
    writeFileSync(calls, "");
    const restart = run("restart");
    assert.equal(restart.status, 0, restart.stderr);
    const trace = readFileSync(calls, "utf8");
    assert.match(trace, /"bootout"/u);
    assert.match(trace, /"bootstrap"/u);
    assert.doesNotMatch(trace, /kickstart/u);
    const logs = run("logs", "-n", "1");
    assert.equal(logs.status, 0, logs.stderr);
    assert.match(logs.stdout, /логов пока нет/u);
    assert.equal(run("stop").status, 0);
    assert.match(run("status").stdout, /iva.service: unloaded/u);
    assert.equal(run("start").status, 0);
    const foreign = join(units, "com.example.other.plist");
    writeFileSync(foreign, "foreign");
    const remove = spawnSync(
      process.execPath,
      [
        "--input-type=module",
        "-e",
        `import {createCliRuntime} from ${JSON.stringify(join(project, "scripts/cli/runtime.ts"))};import {createCliSystemd} from ${JSON.stringify(join(project, "scripts/cli/systemd.ts"))};createCliSystemd(createCliRuntime(${JSON.stringify(project)})).removeUnits();`,
      ],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          HOME: home,
          PATH: `${bin}:/usr/bin:/bin`,
          IVA_STATE: state,
          IVA_CALLS: calls,
        },
      },
    );
    assert.equal(remove.status, 0, remove.stderr);
    assert.deepEqual(readdirSync(units), ["com.example.other.plist"]);
    assert.equal(readFileSync(foreign, "utf8"), "foreign");
    assert.equal(existsSync(join(home, ".config/systemd/user")), false);
    const missing = spawnSync(
      process.execPath,
      [join(project, "bin/iva.mjs"), "status"],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          HOME: home,
          PATH: "/nonexistent",
          NO_COLOR: "1",
        },
      },
    );
    assert.equal(missing.status, 1);
    assert.match(
      missing.stdout + missing.stderr,
      /launchctl print gui\/\d+ failed.*ENOENT/u,
    );
  },
);
