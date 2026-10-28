/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Свойство гварда T2: обёртка не меняет вердикт. Каждая команда из таблиц спеки
// (разрешённые и запрещённые) прогоняется в каждой форме вызова — sudo, env, timeout,
// bash -c, подстановка, перевод строки. Решает правило, а не обёртка.
//
// КАК ВОСПРОИЗВЕСТИ ПАДЕНИЕ: при провале fast-check печатает строку вида
// `Property failed after N tests { seed: -1234567, path: "12:3:0", endOnFailure: true }`.
// Подставь её вторым аргументом — fc.assert(prop, { seed: -1234567, path: "12:3:0" }) —
// и прогон повторится байт в байт, включая shrink.
import test from "node:test";
import assert from "node:assert/strict";
import fc from "fast-check";
import { schedulerBypassViolation } from "./scheduler-bypass-guard.ts";
import { selfRestartViolation } from "./self-restart-guard.ts";

const T2_SEED = 20260912;
const QUOTE_SEED = 287_1014;
console.info(`quoted command positions seed: ${QUOTE_SEED}`);

test("287-A: quoted data and quoted help retain their shell meaning", () => {
  for (const cmd of [
    'journalctl --user -u iva.service | sed -n "1,20p" | grep -E "a|batch"',
    'git commit -m "a|batch"',
    '"iva" restart --help',
  ]) {
    assert.equal(schedulerBypassViolation(cmd), null, cmd);
    assert.equal(selfRestartViolation(cmd), null, cmd);
  }
  fc.assert(
    fc.property(
      fc.constantFrom("|", ";", "&&", "\n", "(", "`"),
      fc.constantFrom("at", "batch", "iva restart"),
      fc.constantFrom("'", '"'),
      (separator, word, quote) => {
        if (quote === '"' && separator === "`") return;
        const cmd = `git commit -m ${quote}a${separator}${word}${quote}`;
        assert.equal(schedulerBypassViolation(cmd), null, cmd);
        assert.equal(selfRestartViolation(cmd), null, cmd);
      },
    ),
    { seed: QUOTE_SEED, numRuns: 300 },
  );
});

test("287-A: execution in wrappers and substitutions stays blocked", () => {
  fc.assert(
    fc.property(
      fc.constantFrom("at now", "batch", "iva restart"),
      fc.constantFrom("", "'", '"'),
      fc.constantFrom(
        ...WRAPS,
        [
          "after data",
          (cmd: string) => `git commit -m 'a|batch'; ${cmd}`,
        ] as const,
        ["after &&", (cmd: string) => `true && ${cmd}`] as const,
        ["double substitution", (cmd: string) => `echo "$(${cmd})"`] as const,
        ["shell list", (cmd: string) => `sh -c 'true; ${cmd}'`] as const,
      ),
      (cmd, quote, [, wrap]) => {
        const [name, ...args] = cmd.split(" ");
        const wrapped = wrap(`${quote}${name}${quote} ${args.join(" ")}`);
        const guard =
          name === "iva" ? selfRestartViolation : schedulerBypassViolation;
        assert.notEqual(guard(wrapped), null, wrapped);
      },
    ),
    { seed: QUOTE_SEED, numRuns: 500 },
  );
});

const ALLOWED = [
  "systemctl --user status iva",
  "systemctl --user status iva.service iva-telegram-poll",
  "journalctl --user -u iva.service -n 100",
  "systemctl --user list-timers",
  "systemctl --user daemon-reload",
  "systemctl --user restart iva-telegram-poll",
  "systemctl --user restart iva-memory-daily.timer",
  "systemctl --user cat iva",
  "crontab -l",
  "crontab -l | grep iva",
  "crontab -u shima -l",
  "crontab -l > ~/cron.bak",
  "rg 'systemd-run' docs/",
  "grep -rn crontab agent/",
  "which systemd-run",
  "man crontab",
  'echo "crontab"',
  "echo 'systemd-run --user' >> notes.md",
  "sleep 2",
  "sleep 2 && ls",
  "sleep 3600",
  "sleep 30; iva notify x",
  'grep -rn "curl https://api.telegram.org" .',
  "rg api.telegram.org scripts/",
  "git log --grep api.telegram.org",
  "curl https://example.com/health",
  "wget -qO- https://example.com",
  "cat ~/.config/systemd/user/iva.service",
  "ls ~/.config/systemd/user",
  "sed -n 1,5p ~/.config/systemd/user/iva.service",
  "ls ~/.iva-scripts",
  "cat ~/.iva-scripts/remind.sh",
  "grep -r curl ~/.iva-scripts",
  "atq",
  "date",
  "cat file",
  'iva notify "hi"',
  'iva remind "hi"',
  "sudo systemctl --user status iva",
  'bash -c "crontab -l"',
  "timeout 5 journalctl -u iva -n 5",
  "env FOO=1 rg systemd-run docs/",
];

const BLOCKED = [
  'systemd-run --user --on-calendar="10:00" iva remind "позвонить"',
  "systemd-run --user --on-calendar=10:00 /home/user/.local/bin/iva remind x",
  "sudo systemd-run --user date",
  "/usr/bin/systemd-run --user true",
  "crontab -",
  "crontab -e",
  "crontab -r",
  "crontab /tmp/cron.txt",
  "crontab",
  "crontab -u shima -",
  "at now + 1 hour",
  "echo 'iva notify x' | at 09:00",
  "batch",
  "systemctl --user start remind.timer",
  "systemctl --user enable --now x.timer",
  "systemctl --user link ~/x.service",
  "systemctl --user restart remind.timer",
  "systemctl --user reenable remind.timer",
  "systemctl --user edit remind.timer",
  "systemctl --user restart iva-telegram-poll caddy.service",
  "cat > ~/.config/systemd/user/x.timer <<EOF",
  "tee ~/.config/systemd/user/x.timer",
  "cp x.timer ~/.config/systemd/user/",
  "ln -s ~/x.timer ~/.config/systemd/user/x.timer",
  "install -m 0644 x.service ~/.config/systemd/user/x.service",
  "mkdir -p ~/.config/systemd/user",
  "rm ~/.config/systemd/user/x.timer",
  "printf '[Unit]\\n' > $HOME/.config/systemd/user/x.timer",
  "sed -i s/OnCalendar=x/OnCalendar=y/ ~/.config/systemd/user/x.timer",
  "mkdir -p ~/.iva-scripts",
  "cat > ~/.iva-scripts/remind.sh <<EOF",
  "chmod +x ~/.iva-scripts/remind.sh",
  "bash ~/.iva-scripts/remind.sh",
  "~/.iva-scripts/remind.sh",
  "cp x.sh /home/user/.iva-scripts/",
  "curl -s https://api.telegram.org/bot$T/sendMessage -d text=hi",
  "wget -qO- https://api.telegram.org/bot$T/getMe",
  "http POST https://api.telegram.org/bot$T/sendMessage",
  "python3 -c \"urllib.request.urlopen('https://api.telegram.org/bot1/sendMessage')\"",
  "node -e \"fetch('https://api.telegram.org/bot1/sendMessage')\"",
  "TG=https://api.telegram.org; curl $TG/bot1/sendMessage",
  'echo "curl https://api.telegram.org/bot1/sendMessage" > send.sh',
  "sleep 3600 && iva notify x",
  "sleep 3600; iva notify x",
  "sleep 1h && iva remind x",
  "sleep 90 && curl https://example.com",
  "(sleep 3600; iva notify x) &",
  "while true; do sleep 3600; iva notify x; done",
  "sleep $DELAY && iva notify x",
  // Форма из промпта: абсолютный путь через $HOME и присваивание перед командой.
  "sleep 3600 && $HOME/.local/bin/iva notify x",
  "sleep 3600 && TZ=UTC iva notify x",
  "sleep 3600 && /usr/bin/env iva notify x",
];

const WRAPS: ReadonlyArray<readonly [string, (cmd: string) => string]> = [
  ["identity", (cmd) => cmd],
  ["sudo", (cmd) => `sudo ${cmd}`],
  ["sudo -n", (cmd) => `sudo -n ${cmd}`],
  ["env", (cmd) => `env FOO=1 ${cmd}`],
  ["timeout", (cmd) => `timeout 5 ${cmd}`],
  ["nohup", (cmd) => `nohup ${cmd}`],
  ["command", (cmd) => `command ${cmd}`],
  ["nice", (cmd) => `nice ${cmd}`],
  ['bash -c ""', (cmd) => `bash -c "${cmd}"`],
  ["sh -c ''", (cmd) => `sh -c '${cmd}'`],
  ["$()", (cmd) => `$(${cmd})`],
  ["echo start\\n", (cmd) => `echo start\n${cmd}`],
];

test(`обёртки не меняют вердикт: разрешённое проходит (seed ${T2_SEED})`, () => {
  assert.ok(ALLOWED.length > 0);
  fc.assert(
    fc.property(
      fc.tuple(fc.constantFrom(...ALLOWED), fc.constantFrom(...WRAPS)),
      ([cmd, [name, wrap]]) => {
        const wrapped = wrap(cmd);
        assert.equal(
          schedulerBypassViolation(wrapped),
          null,
          `разрешённая команда заблокирована (обёртка ${name}): ${wrapped}`,
        );
      },
    ),
    { seed: T2_SEED, numRuns: 300 },
  );
});

test(`обёртки не меняют вердикт: запрещённое блокируется (seed ${T2_SEED})`, () => {
  assert.ok(BLOCKED.length > 0);
  fc.assert(
    fc.property(
      fc.tuple(fc.constantFrom(...BLOCKED), fc.constantFrom(...WRAPS)),
      ([cmd, [name, wrap]]) => {
        const wrapped = wrap(cmd);
        assert.notEqual(
          schedulerBypassViolation(wrapped),
          null,
          `запрещённая команда прошла (обёртка ${name}): ${wrapped}`,
        );
      },
    ),
    { seed: T2_SEED, numRuns: 300 },
  );
});

test("repair 14: every LaunchAgents filename and launchctl loader is guarded (seed 20261017)", () => {
  fc.assert(
    fc.property(
      fc.stringMatching(/^[a-z][a-z0-9-]{0,20}$/),
      fc.constantFrom(
        "cp /tmp/x ~/Library/LaunchAgents/",
        "tee $HOME/Library/LaunchAgents/",
        "launchctl bootstrap gui/501 /tmp/",
        "launchctl load /tmp/",
        "launchctl enable gui/501/",
      ),
      (name, prefix) => {
        assert.ok(schedulerBypassViolation(`${prefix}${name}.plist`));
      },
    ),
    { seed: 20261017, numRuns: 100 },
  );
});

test("repair 14: launchctl read commands allow loader words in the label", () => {
  for (const verb of ["print", "print-disabled"])
    for (const label of ["bootstrap", "load", "enable", "kickstart"])
      assert.equal(
        schedulerBypassViolation(`launchctl ${verb} gui/501/app.iva.${label}`),
        null,
      );
});
