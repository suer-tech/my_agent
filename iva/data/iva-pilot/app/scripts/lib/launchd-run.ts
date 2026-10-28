import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { constants } from "node:os";
import { isEntrypoint } from "./version-layout.ts";

type Auxiliary = { args: string[]; ignore: boolean };
export type LaunchConfig = {
  envFile?: string;
  pre?: Auxiliary[];
  post?: Auxiliary[];
  timeoutSeconds?: number;
};

function signalGroup(pid: number, signal: NodeJS.Signals | 0): boolean {
  try {
    process.kill(-pid, signal);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    // macOS can return EPERM while an orphaned group is being reaped. Keep waiting.
    if (signal === 0 && (error as NodeJS.ErrnoException).code === "EPERM")
      return true;
    throw error;
  }
}

async function auxiliary(commands: Auxiliary[]): Promise<void> {
  for (const item of commands) {
    const child = spawn(item.args[0], item.args.slice(1), {
      stdio: "inherit",
    });
    const code = await new Promise<number>((resolve) => {
      child.once("error", () => resolve(1));
      child.once("exit", (code) => resolve(code ?? 1));
    });
    if (code !== 0 && !item.ignore)
      throw new Error(`command failed (${code}): ${item.args[0]}`);
  }
}

async function waitGroup(pid: number | undefined): Promise<void> {
  if (!pid) return;
  while (signalGroup(pid, 0))
    await new Promise((resolve) => setTimeout(resolve, 25));
}

async function runLaunchdProcess(
  config: LaunchConfig,
  args: readonly string[],
): Promise<number> {
  if (config.envFile && !existsSync(config.envFile)) {
    console.error(`нет ${config.envFile}`);
    return 1;
  }
  let child: ChildProcess;
  try {
    await auxiliary(config.pre ?? []);
    child = spawn(args[0], args.slice(1), {
      detached: true,
      stdio: "inherit",
    });
  } catch (error) {
    console.error(String(error));
    return 1;
  }
  let escalation: ReturnType<typeof setTimeout> | undefined;
  let terminated = false;
  const terminate = (signal: NodeJS.Signals) => {
    if (terminated || !child.pid) return;
    terminated = true;
    signalGroup(child.pid, signal);
    escalation = setTimeout(() => {
      if (child.pid) signalGroup(child.pid, "SIGKILL");
    }, 20_000);
  };
  const term = () => terminate("SIGTERM");
  const interrupt = () => terminate("SIGINT");
  process.on("SIGTERM", term);
  process.on("SIGINT", interrupt);
  const deadline = config.timeoutSeconds
    ? setTimeout(term, config.timeoutSeconds * 1000)
    : undefined;
  const exited = new Promise<number>((resolve) => {
    child.once("error", (error) => {
      console.error(error.message);
      resolve(1);
    });
    child.once("exit", (code, signal) =>
      resolve(code ?? (signal ? 128 + constants.signals[signal] : 1)),
    );
  });
  try {
    await new Promise<void>((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    await auxiliary(config.post ?? []);
    const code = await exited;
    term();
    // The direct child may exit before its grandchildren release inherited lock descriptors.
    await waitGroup(child.pid);
    return code;
  } catch (error) {
    console.error(String(error));
    term();
    await exited;
    await waitGroup(child.pid);
    return 1;
  } finally {
    clearTimeout(deadline);
    clearTimeout(escalation);
    process.off("SIGTERM", term);
    process.off("SIGINT", interrupt);
  }
}

if (isEntrypoint(import.meta.url)) {
  try {
    process.exitCode = await runLaunchdProcess(
      JSON.parse(process.argv[2] ?? "{}") as LaunchConfig,
      process.argv.slice(3),
    );
  } catch (error) {
    console.error(String(error));
    process.exitCode = 1;
  }
}
