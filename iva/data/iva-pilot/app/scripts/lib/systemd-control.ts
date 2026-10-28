import { serviceManager } from "./service-manager.ts";
import { launchdLabel } from "./launchd.ts";
import { spawnSync } from "node:child_process";
import { existsSync, openSync, readSync, fstatSync, closeSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
export { systemdExecArgument } from "./systemd-values.ts";
const UNIT_RE = /^[A-Za-z0-9_.@:-]+$/;

interface CommandResult {
  readonly code?: number | null;
  readonly status?: number | null;
  readonly out?: string | Buffer;
  readonly stdout?: string | Buffer;
}

interface NormalizedResult {
  readonly code: number;
  readonly out: string;
}

interface SystemdControlOptions {
  readonly run: (args: readonly string[]) => CommandResult;
  readonly manager?: "systemd" | "launchd";
}

interface CleanupOptions {
  readonly units: readonly string[];
  readonly disable: (unit: string) => unknown;
  readonly remove: (unit: string) => unknown;
  readonly reload: () => unknown;
  readonly reset: () => unknown;
}

function errorMessage(error: unknown): string {
  const message = (error as { readonly message?: unknown } | null | undefined)
    ?.message;
  return String(message || error);
}

function safeUnit(unit: string): string {
  if (!UNIT_RE.test(unit)) throw new Error(`invalid systemd unit: ${unit}`);
  return unit;
}

function resultOf(result: CommandResult | undefined): NormalizedResult {
  return {
    code: result?.code ?? result?.status ?? 1,
    out: String(result?.out ?? result?.stdout ?? "").trim(),
  };
}

export function logHint(
  unit: string,
  {
    lines,
    pager,
    grep,
  }: { lines?: number; pager?: boolean; grep?: string } = {},
  manager = serviceManager(),
): string {
  const command =
    manager === "launchd"
      ? `tail -n ${lines ?? 100} ~/Library/Logs/Iva/${launchdLabel(safeUnit(unit)).slice(8)}.out.log`
      : `journalctl --user -u ${safeUnit(unit)}${lines === undefined ? "" : ` -n ${lines}`}${pager ? " --no-pager" : ""}`;
  return `${command}${grep ? ` | grep ${grep}` : ""}`;
}

export function logTail(unit: string, lines: number): string {
  if (serviceManager() !== "launchd")
    return (
      spawnSync(
        "journalctl",
        [
          "--user",
          "-u",
          safeUnit(unit),
          "-n",
          String(lines),
          "--no-pager",
          "-o",
          "cat",
        ],
        { encoding: "utf8" },
      ).stdout || ""
    );
  return logPaths(unit)
    .filter(existsSync)
    .map((file) =>
      readLogSuffix(file).trimEnd().split("\n").slice(-lines).join("\n"),
    )
    .join("\n");
}

function readLogSuffix(file: string): string {
  const fd = openSync(file, "r");
  try {
    const size = fstatSync(fd).size;
    const buffer = Buffer.alloc(Math.min(size, 64 * 1024));
    const count = readSync(
      fd,
      buffer,
      0,
      buffer.length,
      Math.max(0, size - buffer.length),
    );
    return buffer.subarray(0, count).toString("utf8");
  } finally {
    closeSync(fd);
  }
}

export function logPaths(unit: string): string[] {
  const base = join(
    homedir(),
    "Library/Logs/Iva",
    launchdLabel(safeUnit(unit)).slice(8),
  );
  return [`${base}.out.log`, `${base}.err.log`];
}

export function restartFailure(text: string): string {
  if (serviceManager() !== "launchd") return text;
  return /[А-Яа-я]/u.test(text)
    ? "Не удалось перезапустить — `iva logs`"
    : "Couldn't restart — `iva logs`";
}

export function controlFailure(
  args: readonly string[],
  manager = serviceManager(),
): string {
  if (manager !== "launchd") return `systemctl --user ${args.join(" ")} failed`;
  const unit = args.find((arg) => /\.(service|timer)$/u.test(arg));
  const verb =
    (
      { restart: "kickstart", start: "bootstrap", stop: "bootout" } as Record<
        string,
        string
      >
    )[args[0]] ?? args[0];
  return `launchctl ${verb}${unit ? ` ${launchdLabel(unit)}` : ""} failed`;
}

export class SystemdControlError extends Error {
  declare readonly unit: string | undefined;
  declare readonly code: number | undefined;

  constructor(
    message: string,
    {
      unit,
      code,
      manager,
    }: { unit?: string; code?: number; manager?: "systemd" | "launchd" } = {},
  ) {
    const suffix = unit
      ? ` Check: ${logHint(unit, { lines: 100, pager: true }, manager)}`
      : "";
    super(`${message}${code === undefined ? "" : ` (exit ${code})`}.${suffix}`);
    this.name = "SystemdControlError";
    this.unit = unit;
    this.code = code;
  }
}

export function cleanupSystemdUnits({
  units,
  disable,
  remove,
  reload,
  reset,
}: CleanupOptions): string[] {
  const errors: Error[] = [];
  const attempt = (label: string, action: () => unknown): void => {
    try {
      action();
    } catch (cause) {
      errors.push(new Error(`${label}: ${errorMessage(cause)}`, { cause }));
    }
  };

  const checkedUnits = units.map(safeUnit);
  for (const unit of checkedUnits)
    attempt(`disable ${unit}`, () => disable(unit));
  for (const unit of checkedUnits)
    attempt(`remove ${unit}`, () => remove(unit));
  attempt("daemon-reload", reload);
  attempt("reset-failed", reset);

  if (errors.length > 0) {
    const shown = errors
      .slice(0, 8)
      .map((error) => error.message.replace(/\s+/g, " ").slice(0, 240));
    const omitted = errors.length - shown.length;
    const detail = `${shown.join("; ")}${omitted > 0 ? `; ${omitted} more failure(s)` : ""}`;
    throw new AggregateError(errors, `systemd unit cleanup failed: ${detail}`);
  }
  return checkedUnits;
}

export function createSystemdControl({
  run,
  manager = "systemd",
}: SystemdControlOptions) {
  if (typeof run !== "function")
    throw new TypeError("systemd control requires run(args)");

  const query = (...args: string[]): NormalizedResult => resultOf(run(args));

  function mutate(args: string[], unit?: string): NormalizedResult {
    const result = query(...args);
    if (result.code !== 0) {
      throw new SystemdControlError(
        manager === "launchd"
          ? result.out || controlFailure(args, manager)
          : controlFailure(args, manager),
        {
          unit,
          code: result.code,
          manager,
        },
      );
    }
    return result;
  }

  function isEnabled(unit: string): boolean {
    safeUnit(unit);
    const result = query("is-enabled", unit);
    return result.code === 0 && result.out === "enabled";
  }

  function isActive(unit: string): boolean {
    safeUnit(unit);
    const result = query("is-active", unit);
    return result.code === 0 && result.out === "active";
  }

  function requireEnabledActive(unit: string): void {
    safeUnit(unit);
    if (!isEnabled(unit)) {
      throw new SystemdControlError(`${unit} did not become enabled`, {
        unit,
        manager,
      });
    }
    if (!isActive(unit)) {
      throw new SystemdControlError(`${unit} did not become active`, {
        unit,
        manager,
      });
    }
  }

  const control = {
    query,
    isEnabled,
    isActive,
    activate(units: readonly string[]): void {
      for (const unit of units) {
        safeUnit(unit);
        mutate(["enable", "--now", unit], unit);
        requireEnabledActive(unit);
      }
    },
    restart(units: readonly string[]): void {
      for (const unit of units) {
        safeUnit(unit);
        mutate(["restart", unit], unit);
        if (!isActive(unit)) {
          throw new SystemdControlError(
            `${unit} did not become active after restart`,
            { unit, manager },
          );
        }
      }
    },
    stop(units: readonly string[]): void {
      for (const unit of units) {
        safeUnit(unit);
        mutate(["stop", unit], unit);
      }
    },
    disableNow(units: readonly string[]): void {
      for (const unit of units) {
        safeUnit(unit);
        mutate(["disable", "--now", unit], unit);
      }
    },
    resetFailed(units: readonly string[] = []): NormalizedResult | void {
      if (units.length === 0) return mutate(["reset-failed"]);
      for (const unit of units) {
        safeUnit(unit);
        mutate(["reset-failed", unit], unit);
      }
    },
    daemonReload() {
      return mutate(["daemon-reload"]);
    },
  };
  return Object.assign(control, { manager }) as typeof control & {
    manager?: "systemd" | "launchd";
  };
}
