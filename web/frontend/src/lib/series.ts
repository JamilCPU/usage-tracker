import type { DailyRow, HourlyRow } from "./api";

export const UNKNOWN_APP = "(unknown)";
export const OTHER = "other";

export type HourlySeries = {
  /** Series names in stacking order; the last may be "other". */
  names: string[];
  /** hours[h][name] = seconds */
  hours: Record<string, number>[];
  maxTotal: number;
};

/** Hour of day taken from the ISO string itself (server-local time, offset included). */
export function bucketHour(bucket: string): number {
  return Number(bucket.slice(11, 13));
}

export function buildHourly(rows: HourlyRow[], topN = 8): HourlySeries {
  const totals = new Map<string, number>();
  for (const r of rows) {
    const app = r.app ?? UNKNOWN_APP;
    totals.set(app, (totals.get(app) ?? 0) + r.seconds);
  }
  const top = [...totals.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, topN)
    .map(([n]) => n);
  const topSet = new Set(top);
  const hours: Record<string, number>[] = Array.from({ length: 24 }, () => ({}));
  let hasOther = false;
  for (const r of rows) {
    const h = bucketHour(r.bucket);
    const bucket = hours[h];
    if (!bucket) continue;
    const app = r.app ?? UNKNOWN_APP;
    const name = topSet.has(app) ? app : OTHER;
    if (name === OTHER) hasOther = true;
    bucket[name] = (bucket[name] ?? 0) + r.seconds;
  }
  const maxTotal = Math.max(
    0,
    ...hours.map((b) => Object.values(b).reduce((a, c) => a + c, 0)),
  );
  return { names: hasOther ? [...top, OTHER] : top, hours, maxTotal };
}

export function totalsByDevice(rows: DailyRow[]): { device: string; seconds: number }[] {
  const m = new Map<string, number>();
  for (const r of rows) m.set(r.device, (m.get(r.device) ?? 0) + r.seconds);
  return [...m.entries()].map(([device, seconds]) => ({ device, seconds })).sort((a, b) => a.device.localeCompare(b.device));
}
