// Wall-clock arithmetic in the owner's time zone: the one copy of it. The schedule
// catch-up in schedule-migration.ts, the /menu screen (T4) and the reminders tool (T5)
// all show or place a due point in the owner's zone with the same math, so it lives here
// instead of twice.
//
// ── timezone-aware "most recent due point" math ─────────────────────────────
// No Temporal/date library dependency: derive local wall-clock Y-M-D-H-M from Intl, then
// convert a candidate local wall-clock point back to a UTC epoch by the standard
// guess-and-correct trick (good to the minute, which is all a memory rollup needs).
export function zonedParts(
  epochMs: number,
  tz: string,
): { y: number; m: number; d: number; hh: number; mm: number; ss: number } {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const parts = Object.fromEntries(
    fmt.formatToParts(new Date(epochMs)).map((p) => [p.type, p.value]),
  );
  // Midnight sometimes formats as "24" in en-US hour12:false — normalize to 0.
  const hour = parts.hour === "24" ? 0 : Number(parts.hour);
  return {
    y: Number(parts.year),
    m: Number(parts.month),
    d: Number(parts.day),
    hh: hour,
    mm: Number(parts.minute),
    ss: Number(parts.second),
  };
}

export function zonedToUtcMs(
  y: number,
  m: number,
  d: number,
  hh: number,
  mm: number,
  tz: string,
): number {
  let guess = Date.UTC(y, m - 1, d, hh, mm, 0);
  for (let i = 0; i < 3; i++) {
    const seen = zonedParts(guess, tz);
    const seenAsUtc = Date.UTC(
      seen.y,
      seen.m - 1,
      seen.d,
      seen.hh,
      seen.mm,
      seen.ss,
    );
    const wantAsUtc = Date.UTC(y, m - 1, d, hh, mm, 0);
    const diff = seenAsUtc - wantAsUtc;
    if (diff === 0) break;
    guess -= diff;
  }
  return guess;
}

export function addDaysToDate(
  y: number,
  m: number,
  d: number,
  days: number,
): { y: number; m: number; d: number } {
  const shifted = new Date(Date.UTC(y, m - 1, d, 12) + days * 86_400_000); // noon avoids DST edge cases
  return {
    y: shifted.getUTCFullYear(),
    m: shifted.getUTCMonth() + 1,
    d: shifted.getUTCDate(),
  };
}

// "YYYY-MM-DD HH:mm" на стене владельца - формат, в котором /menu и тул показывают срок.
export function formatZoned(epochMs: number, tz: string): string {
  const { y, m, d, hh, mm } = zonedParts(epochMs, tz);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${y}-${pad(m)}-${pad(d)} ${pad(hh)}:${pad(mm)}`;
}
