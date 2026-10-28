type LockOptions = { file: string; command?: string } & (
  { waitSeconds: number } | { nonBlocking: true }
);

export function lockCommand(
  options: LockOptions,
  platform: NodeJS.Platform = process.platform,
): { command: string; args: string[] } {
  if (platform === "darwin")
    return {
      command: "/usr/bin/lockf",
      args: [
        "-t",
        "waitSeconds" in options ? String(options.waitSeconds) : "0",
        options.file,
      ],
    };
  return {
    command: options.command ?? "flock",
    args: [
      ...("waitSeconds" in options
        ? ["-w", String(options.waitSeconds)]
        : ["-n", "-E", "75"]),
      options.file,
    ],
  };
}
