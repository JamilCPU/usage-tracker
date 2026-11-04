import { toQuery, type Filters } from "./filters";

export type DeviceMeta = { device: string; first_seen: string; last_seen: string; events: number };
export type Meta = { tz: string; devices: DeviceMeta[] };
export type HourlyRow = { bucket: string; device: string; app: string | null; seconds: number };
export type Hourly = { from: string; to: string; tz: string; rows: HourlyRow[] };
export type TopApp = { app: string | null; seconds: number };
export type TopDomain = { domain: string | null; seconds: number };
export type DailyRow = { day: string; device: string; seconds: number };
export type UsageEvent = {
  id: number;
  device: string;
  ts_start: string;
  ts_end: string;
  kind: "window" | "web";
  app: string | null;
  title: string | null;
  url: string | null;
  domain: string | null;
};
export type EventsPage = { rows: UsageEvent[]; next_cursor: string | null };

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string): Promise<T> {
  if (import.meta.env.VITE_MOCK === "1") {
    const { mockRequest } = await import("./mock");
    return mockRequest(path) as T;
  }
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (res.status === 401) {
    const next = encodeURIComponent(location.pathname + location.search);
    location.assign(`/auth/login?next=${next}`);
    return new Promise<T>(() => {}); // navigation in progress; never resolves
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function withExtra(f: Filters, extra: Record<string, string | number>): string {
  const p = new URLSearchParams(toQuery(f));
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

export const getMeta = () => request<Meta>("/api/meta");
/** The chart needs every hour so out-of-range hours can be dimmed client-side. */
export function hourlyQuery(f: Filters): string {
  return toQuery({ ...f, hourFrom: 0, hourTo: 23 });
}

export const getHourly = (f: Filters) => request<Hourly>(`/api/hourly?${hourlyQuery(f)}`);
export const getTopApps = (f: Filters, limit = 20) =>
  request<{ rows: TopApp[] }>(`/api/top/apps?${withExtra(f, { limit })}`);
export const getTopDomains = (f: Filters, limit = 20) =>
  request<{ rows: TopDomain[] }>(`/api/top/domains?${withExtra(f, { limit })}`);
export const getDaily = (f: Filters) => request<{ rows: DailyRow[] }>(`/api/daily?${toQuery(f)}`);
export const getEvents = (f: Filters, cursor: string | null, limit = 100) =>
  request<EventsPage>(`/api/events?${withExtra(f, cursor ? { cursor, limit } : { limit })}`);
