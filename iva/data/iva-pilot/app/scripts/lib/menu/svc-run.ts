import { serviceManager } from "../service-manager.ts";
import { runLaunchd } from "../launchd.ts";
import { logTail } from "../systemd-control.ts";
// Раннер экрана «Обслуживание» (svc): один долгий процесс на мост + живой прогресс в том же
// сообщении меню. Спека: notes/specs/2026-07-25-menu-service-design.md (вне публичного дерева, см. историю git).
//
// - spawn-команды (доктор, чистка) живут в cgroup моста — рестарт моста честно убивает их;
// - ночной цикл — штатный oneshot-юнит: старт --no-block, статус поллингом is-active
//   (activating → inactive|failed), переживает рестарт моста, отмена = systemctl stop;
// - тикер только собирает markdown и отдаёт его движку через opts.edit (flows.screen):
//   своей дороги в Telegram у экрана нет, кнопка отмены живёт строкой в тексте.
import {
  spawn,
  execFile,
  type ChildProcess,
  type ExecFileOptions,
} from "node:child_process";

// Лоадер: своим цветом помечает, какая команда бежит. Не перевод.
export const LOADERS = {
  doc: { alt: "🔄" },
  cln: { alt: "🟢" },
  mem: { alt: "🟡" },
};
export const TIMEOUT_MS = { doc: 600_000, cln: 1_800_000, mem: 3_600_000 };
const TICK_MS = 3_000;
const POLL_MS = 3_000;
const TAIL_LINES = 40;

type ServiceCommand = keyof typeof TIMEOUT_MS;
type RunStatus = "running" | "failed" | "cancelled" | "timeout" | "done";

interface ProgressView {
  text: string;
}

export interface RunOptions {
  // Единственная дорога к сообщению — движок: он правит rich-текст в st.msgId
  // (flows.screen) и сам разбирается с «message is not modified» и протухшим сообщением.
  edit: (markdown: string) => Promise<unknown>;
  chatId: number | string;
  messageId: number;
  attached?: () => boolean;
  progressView: (run: ServiceRun) => ProgressView;
  onFinish?: (run: ServiceRun) => void | Promise<void>;
  tickMs?: number;
  timeoutMs?: number;
  pollMs?: number;
  spawnImpl?: typeof spawn;
  execFileImpl?: ExecFileImplementation;
}

interface ProcessSpec {
  argv: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
}

interface UnitSpec {
  unit: string;
}

export interface ServiceRun {
  cmd: ServiceCommand;
  status: RunStatus;
  startedAt: number;
  finishedAt: number | null;
  lastLine: string;
  tail: string[];
  cancelled: boolean;
  timedOut: boolean;
  chatId: number | string;
  messageId: number;
  _edit: { lastPayload: string };
  _timer: NodeJS.Timeout | null;
  _kill: (() => void) | null;
}
type ExecFileCallback = (
  error: (Error & { code?: string | number | null }) | null,
  stdout: string | Buffer,
  stderr: string | Buffer,
) => void;

export type ExecFileImplementation = (
  file: string,
  args: string[],
  options: ExecFileOptions & { encoding: "utf8" },
  callback: ExecFileCallback,
) => unknown;

function errorMessage(error: unknown): string {
  const withMessage = error as { message?: unknown };
  return String(withMessage.message || error);
}

let RUN: ServiceRun | null = null; // единственный запуск на мост

export const currentRun = () => RUN;
export function resetForTests() {
  RUN = null;
}

export function stripAnsi(s: unknown): string {
  // eslint-disable-next-line no-control-regex -- This helper intentionally strips ANSI escape sequences.
  const withoutAnsi = String(s).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
  return withoutAnsi.replace(/\r/g, "");
}

export function elapsed(
  run: Pick<ServiceRun, "startedAt" | "finishedAt">,
): string {
  const s = Math.max(
    0,
    Math.floor(((run.finishedAt ?? Date.now()) - run.startedAt) / 1000),
  );
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

// Хвост вывода с конца, суммарно не длиннее limit (запас от лимита 4096 editMessageText).
export function tailText(run: Pick<ServiceRun, "tail">, limit = 1500): string {
  const out = [];
  let len = 0;
  for (let i = run.tail.length - 1; i >= 0; i--) {
    const line = run.tail[i];
    if (len + line.length + 1 > limit) break;
    out.unshift(line);
    len += line.length + 1;
  }
  return out.join("\n");
}

function baseRun(cmd: ServiceCommand, opts: RunOptions): ServiceRun {
  return {
    cmd,
    status: "running",
    startedAt: Date.now(),
    finishedAt: null,
    lastLine: "",
    tail: [],
    cancelled: false,
    timedOut: false,
    chatId: opts.chatId,
    messageId: opts.messageId,
    _edit: { lastPayload: "" },
    _timer: null,
    _kill: null,
  };
}

function pushLines(
  run: Pick<ServiceRun, "lastLine" | "tail">,
  chunk: Buffer | string,
): void {
  for (const line of stripAnsi(chunk.toString())
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)) {
    run.lastLine = line;
    run.tail.push(line);
    if (run.tail.length > TAIL_LINES) run.tail.shift();
  }
}

// Эдит прогресса: markdown уже собран экраном (кнопка отмены — строкой в тексте),
// тикер только отсеивает повторы и отдаёт его движку.
async function editProgress(
  opts: RunOptions,
  run: ServiceRun,
  markdown: string,
): Promise<void> {
  if (run._edit.lastPayload === markdown) return;
  run._edit.lastPayload = markdown;
  await opts.edit(markdown).catch(() => {});
}

function startTicker(run: ServiceRun, opts: RunOptions): void {
  const tick = async () => {
    if (run.status !== "running") {
      if (run._timer) clearInterval(run._timer);
      return;
    }
    if (opts.attached && !opts.attached()) return; // юзер ушёл с экрана — не дерёмся за сообщение
    const v = opts.progressView(run);
    await editProgress(opts, run, v.text);
  };
  run._timer = setInterval(() => {
    void tick();
  }, opts.tickMs ?? TICK_MS);
  if (run._timer.unref) run._timer.unref();
  void tick();
}

function finish(run: ServiceRun, status: RunStatus, opts: RunOptions): void {
  if (run.status !== "running") return;
  run.status = status;
  run.finishedAt = Date.now();
  if (run._timer) clearInterval(run._timer);
  void Promise.resolve()
    .then(() => opts.onFinish?.(run))
    .catch(() => {});
}

export function startProcess(
  cmd: ServiceCommand,
  spec: ProcessSpec,
  opts: RunOptions,
): ServiceRun | null {
  if (RUN && RUN.status === "running") return null;
  const run = (RUN = baseRun(cmd, opts));
  let child: ChildProcess;
  try {
    child = (opts.spawnImpl ?? spawn)(spec.argv[0], spec.argv.slice(1), {
      cwd: spec.cwd,
      env: spec.env ?? process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    pushLines(run, errorMessage(error));
    finish(run, "failed", opts);
    return run;
  }
  run._kill = () => {
    try {
      child.kill("SIGTERM");
    } catch {
      // The child may already have exited before cancellation reaches it.
    }
  };
  child.stdout!.on("data", (chunk: Buffer | string) => pushLines(run, chunk));
  child.stderr!.on("data", (chunk: Buffer | string) => pushLines(run, chunk));
  const timer = setTimeout(() => {
    run.timedOut = true;
    run._kill!();
  }, opts.timeoutMs ?? TIMEOUT_MS[cmd]);
  if (timer.unref) timer.unref();
  child.on("error", (error) => {
    clearTimeout(timer);
    pushLines(run, errorMessage(error));
    finish(run, "failed", opts);
  });
  child.on("close", (code) => {
    clearTimeout(timer);
    const status = run.cancelled
      ? "cancelled"
      : run.timedOut
        ? "timeout"
        : code === 0
          ? "done"
          : "failed";
    finish(run, status, opts);
  });
  startTicker(run, opts);
  return run;
}

export function startUnit(
  cmd: ServiceCommand,
  { unit }: UnitSpec,
  opts: RunOptions,
): ServiceRun | null {
  if (RUN && RUN.status === "running") return null;
  const run = (RUN = baseRun(cmd, opts));
  const ex: ExecFileImplementation =
    opts.execFileImpl ??
    ((file, args, options, callback) => {
      execFile(file, args, options, callback);
    });
  const sysctl = (args: string[]): Promise<{ code: number; out: string }> =>
    !opts.execFileImpl && serviceManager() === "launchd"
      ? Promise.resolve(runLaunchd(args))
      : new Promise((resolve) =>
          ex(
            "systemctl",
            ["--user", ...args],
            { timeout: 15_000, encoding: "utf8" },
            (error, stdout = "") =>
              resolve({
                code: error
                  ? typeof error.code === "number"
                    ? error.code
                    : 1
                  : 0,
                out: String(stdout).trim(),
              }),
          ),
        );
  const journal = (): Promise<string[]> =>
    !opts.execFileImpl && serviceManager() === "launchd"
      ? Promise.resolve(logTail(unit, 15).split("\n").filter(Boolean))
      : new Promise((resolve) =>
          ex(
            "journalctl",
            ["--user", "-u", unit, "-n", "15", "--no-pager", "-o", "cat"],
            { timeout: 15_000, encoding: "utf8" },
            (_error, stdout = "") =>
              resolve(
                stripAnsi(String(stdout))
                  .split("\n")
                  .map((l) => l.trim())
                  .filter(Boolean),
              ),
          ),
        );
  run._kill = () => {
    void sysctl(["stop", unit]);
  };
  const wait = (ms: number): Promise<void> =>
    new Promise((resolve) => setTimeout(resolve, ms));
  void (async () => {
    const started = await sysctl(["start", "--no-block", unit]);
    if (started.code !== 0) {
      pushLines(run, started.out || "systemctl start failed");
      return finish(run, "failed", opts);
    }
    const deadline = Date.now() + (opts.timeoutMs ?? TIMEOUT_MS[cmd]);
    for (;;) {
      await wait(opts.pollMs ?? POLL_MS);
      if (run.status !== "running") return;
      const st = await sysctl(["is-active", unit]);
      if (
        ["activating", "active", "reloading", "deactivating"].includes(st.out)
      ) {
        if (Date.now() > deadline) return finish(run, "timeout", opts); // юнит НЕ убиваем
        continue;
      }
      run.tail = await journal(); // inactive|failed — конец, итог из журнала
      if (run.cancelled) return finish(run, "cancelled", opts);
      return finish(run, st.out === "failed" ? "failed" : "done", opts);
    }
  })();
  startTicker(run, opts);
  return run;
}

export function cancelRun() {
  if (!RUN || RUN.status !== "running") return false;
  RUN.cancelled = true;
  RUN._kill!();
  return true;
}
