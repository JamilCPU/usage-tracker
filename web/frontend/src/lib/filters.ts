export type Filters = {
  devices: string[];
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD, exclusive
  hourFrom: number;
  hourTo: number;
  app: string;
  domain: string;
};

export type Preset = { label: string; from: string; to: string };

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export function ymd(d: Date): string {
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

export function presets(today: Date): Preset[] {
  const tomorrow = ymd(addDays(today, 1));
  return [
    { label: "Today", from: ymd(today), to: tomorrow },
    { label: "Yesterday", from: ymd(addDays(today, -1)), to: ymd(today) },
    { label: "Last 7 days", from: ymd(addDays(today, -6)), to: tomorrow },
    { label: "Last 30 days", from: ymd(addDays(today, -29)), to: tomorrow },
  ];
}

export function defaultFilters(today: Date): Filters {
  return {
    devices: [],
    from: ymd(today),
    to: ymd(addDays(today, 1)),
    hourFrom: 0,
    hourTo: 23,
    app: "",
    domain: "",
  };
}

export function toQuery(f: Filters): string {
  const p = new URLSearchParams();
  for (const d of f.devices) p.append("device", d);
  p.set("from", f.from);
  p.set("to", f.to);
  p.set("hour_from", String(f.hourFrom));
  p.set("hour_to", String(f.hourTo));
  if (f.app) p.set("app", f.app);
  if (f.domain) p.set("domain", f.domain);
  return p.toString();
}

function parseHour(raw: string | null, fallback: number): number {
  if (raw === null || raw.trim() === "") return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(23, Math.max(0, Math.trunc(n)));
}

function parseDay(raw: string | null, fallback: string): string {
  return raw !== null && DAY_RE.test(raw) ? raw : fallback;
}

export function fromQuery(q: string, defaults: Filters): Filters {
  const p = new URLSearchParams(q);
  return {
    devices: p.getAll("device").filter((d) => d !== ""),
    from: parseDay(p.get("from"), defaults.from),
    to: parseDay(p.get("to"), defaults.to),
    hourFrom: parseHour(p.get("hour_from"), defaults.hourFrom),
    hourTo: parseHour(p.get("hour_to"), defaults.hourTo),
    app: p.get("app") ?? defaults.app,
    domain: p.get("domain") ?? defaults.domain,
  };
}

/** Keep the address bar in sync with the filters without adding history entries. */
export function syncUrl(f: Filters): void {
  const url = `${location.pathname}?${toQuery(f)}`;
  history.replaceState(null, "", url);
}
