// Deterministic fake data for UI development only (VITE_MOCK=1).
import type { DailyRow, EventsPage, HourlyRow, Meta } from "./api";

const DEVICES = ["desk-a", "desk-b"];
const APPS = ["editor", "browser", "terminal", "chat", "player", "mail", "notes", "files", "calc", "maps"];
const DOMAINS = ["docs.example.com", "video.example.org", "news.example.net", "mail.example.com"];
const TZ_OFFSET = "-06:00";

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296;
}

function days(from: string, to: string): string[] {
  const out: string[] = [];
  const d = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  while (d < end && out.length < 60) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

function pick(p: URLSearchParams) {
  const today = new Date().toISOString().slice(0, 10);
  const devs = p.getAll("device");
  return {
    from: p.get("from") ?? today,
    to: p.get("to") ?? today,
    devices: devs.length ? devs : DEVICES,
  };
}

function hourlyRows(p: URLSearchParams): HourlyRow[] {
  const { from, to, devices } = pick(p);
  const rows: HourlyRow[] = [];
  const r = rng(7);
  for (const day of days(from, to)) {
    for (const device of devices) {
      for (let h = 7; h < 23; h++) {
        if (r() < 0.25) continue; // gaps
        const n = 1 + Math.floor(r() * 3);
        for (let i = 0; i < n; i++) {
          const app = APPS[Math.floor(r() * r() * APPS.length)] ?? "editor";
          rows.push({
            bucket: `${day}T${String(h).padStart(2, "0")}:00:00${TZ_OFFSET}`,
            device,
            app,
            seconds: Math.round(300 + r() * 1500),
          });
        }
      }
    }
  }
  return rows;
}

function topApps(p: URLSearchParams, limit: number) {
  const m = new Map<string, number>();
  for (const r of hourlyRows(p)) m.set(r.app ?? "", (m.get(r.app ?? "") ?? 0) + r.seconds);
  return [...m.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([app, seconds]) => ({ app, seconds }));
}

export function mockRequest(path: string): unknown {
  const [route = "", query = ""] = path.split("?");
  const p = new URLSearchParams(query);
  const limit = Number(p.get("limit") ?? 20);
  switch (route) {
    case "/api/meta": {
      const meta: Meta = {
        tz: "America/Chicago",
        devices: DEVICES.map((device, i) => ({
          device,
          first_seen: "2026-09-01T08:00:00-05:00",
          last_seen: "2026-11-04T09:00:00-06:00",
          events: 12000 + i * 3000,
        })),
      };
      return meta;
    }
    case "/api/hourly": {
      const { from, to } = pick(p);
      return {
        from: `${from}T00:00:00${TZ_OFFSET}`,
        to: `${to}T00:00:00${TZ_OFFSET}`,
        tz: "America/Chicago",
        rows: hourlyRows(p),
      };
    }
    case "/api/top/apps":
      return { rows: topApps(p, limit) };
    case "/api/top/domains": {
      const r = rng(3);
      return {
        rows: DOMAINS.map((domain) => ({ domain, seconds: Math.round(600 + r() * 9000) }))
          .sort((a, b) => b.seconds - a.seconds)
          .slice(0, limit),
      };
    }
    case "/api/daily": {
      const { from, to, devices } = pick(p);
      const r = rng(11);
      const rows: DailyRow[] = [];
      for (const day of days(from, to))
        for (const device of devices) rows.push({ day, device, seconds: Math.round(7200 + r() * 25000) });
      return { rows };
    }
    case "/api/events": {
      const start = Number(p.get("cursor") ?? 0);
      const rows = Array.from({ length: Math.min(limit, 25) }, (_, i) => {
        const id = start + i + 1;
        const web = id % 3 === 0;
        const mm = String(id % 60).padStart(2, "0");
        return {
          id,
          device: DEVICES[id % 2] ?? "desk-a",
          ts_start: `2026-11-04T10:${mm}:00${TZ_OFFSET}`,
          ts_end: `2026-11-04T10:${mm}:40${TZ_OFFSET}`,
          kind: web ? ("web" as const) : ("window" as const),
          app: APPS[id % APPS.length] ?? null,
          title: web ? `<b>Page ${id}</b> & notes` : `Document ${id}`,
          url: web ? `https://docs.example.com/p/${id}` : null,
          domain: web ? "docs.example.com" : null,
        };
      });
      const page: EventsPage = {
        rows,
        next_cursor: start + rows.length < 100 ? String(start + rows.length) : null,
      };
      return page;
    }
    default:
      throw new Error(`mock: unknown route ${route}`);
  }
}
