import { describe, expect, it } from "vitest";
import { buildHourly, totalsByDevice } from "./series";

describe("buildHourly", () => {
  const row = (h: number, app: string | null, seconds: number) => ({
    bucket: `2026-11-04T${String(h).padStart(2, "0")}:00:00-06:00`,
    device: "desk-a",
    app,
    seconds,
  });
  it("keeps top N and folds the rest into other", () => {
    const rows = Array.from({ length: 10 }, (_, i) => row(9, `app${i}`, 100 - i));
    const s = buildHourly(rows, 8);
    expect(s.names).toHaveLength(9);
    expect(s.names.at(-1)).toBe("other");
    expect(s.hours[9]?.other).toBe(100 - 8 + 100 - 9);
  });
  it("leaves empty hours empty and names null apps", () => {
    const s = buildHourly([row(3, null, 60)]);
    expect(s.hours[4]).toEqual({});
    expect(s.names).toEqual(["(unknown)"]);
    expect(s.maxTotal).toBe(60);
  });
});

describe("totalsByDevice", () => {
  it("sums per device", () => {
    const t = totalsByDevice([
      { day: "2026-11-01", device: "b", seconds: 5 },
      { day: "2026-11-02", device: "b", seconds: 7 },
      { day: "2026-11-02", device: "a", seconds: 1 },
    ]);
    expect(t).toEqual([
      { device: "a", seconds: 1 },
      { device: "b", seconds: 12 },
    ]);
  });
});
