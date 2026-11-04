import { describe, expect, it } from "vitest";
import { defaultFilters, fromQuery, presets, toQuery, type Filters } from "./filters";

const today = new Date(2026, 10, 4, 15, 30); // 2026-11-04 local
const defaults = defaultFilters(today);

describe("toQuery / fromQuery", () => {
  it("round trips with repeated devices", () => {
    const f: Filters = {
      devices: ["desk-a", "desk-b"],
      from: "2026-10-01",
      to: "2026-10-08",
      hourFrom: 6,
      hourTo: 22,
      app: "editor",
      domain: "example.com",
    };
    const q = toQuery(f);
    expect(q.match(/device=/g)).toHaveLength(2);
    expect(fromQuery(q, defaults)).toEqual(f);
  });

  it("omits empty app/domain but always includes range and hours", () => {
    const q = toQuery({ ...defaults, app: "", domain: "" });
    expect(q).not.toContain("app=");
    expect(q).not.toContain("domain=");
    for (const k of ["from=", "to=", "hour_from=", "hour_to="]) expect(q).toContain(k);
  });

  it("clamps hours and falls back on bad numbers", () => {
    const f = fromQuery("hour_from=-5&hour_to=99", defaults);
    expect(f.hourFrom).toBe(0);
    expect(f.hourTo).toBe(23);
    const g = fromQuery("hour_from=abc&hour_to=", defaults);
    expect(g.hourFrom).toBe(defaults.hourFrom);
    expect(g.hourTo).toBe(defaults.hourTo);
  });

  it("falls back on bad dates and accepts a leading ?", () => {
    const f = fromQuery("?from=nope&to=2026-11-02", defaults);
    expect(f.from).toBe(defaults.from);
    expect(f.to).toBe("2026-11-02");
  });
});

describe("presets", () => {
  const p = Object.fromEntries(presets(today).map((x) => [x.label, x]));
  it("Today ends tomorrow (exclusive)", () => {
    expect(p["Today"]).toMatchObject({ from: "2026-11-04", to: "2026-11-05" });
  });
  it("Yesterday", () => {
    expect(p["Yesterday"]).toMatchObject({ from: "2026-11-03", to: "2026-11-04" });
  });
  it("Last 7 / 30 days include today", () => {
    expect(p["Last 7 days"]).toMatchObject({ from: "2026-10-29", to: "2026-11-05" });
    expect(p["Last 30 days"]).toMatchObject({ from: "2026-10-06", to: "2026-11-05" });
  });
  it("crosses month and year boundaries", () => {
    const q = Object.fromEntries(presets(new Date(2026, 0, 2)).map((x) => [x.label, x]));
    expect(q["Last 7 days"]).toMatchObject({ from: "2025-12-27", to: "2026-01-03" });
  });
});
