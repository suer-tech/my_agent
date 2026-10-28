import { spawnSync } from "node:child_process";

function hasSystemctlOnPath(): boolean {
  return (
    spawnSync("/bin/sh", ["-c", "command -v systemctl"], { encoding: "utf8" })
      .status === 0
  );
}

export function serviceManager({
  platform = process.platform,
  systemctlOnPath = hasSystemctlOnPath,
}: {
  platform?: NodeJS.Platform;
  systemctlOnPath?: () => boolean;
} = {}): "systemd" | "launchd" | null {
  return systemctlOnPath()
    ? "systemd"
    : platform === "darwin"
      ? "launchd"
      : null;
}
