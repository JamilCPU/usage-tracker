import { describe, expect, it } from "vitest";
import { fmtDay, fmtDuration, hourLabel, palette } from "./format";

describe("fmtDuration", () => {
  it.each([
    [0, "0m"],
    [59, "0m"],
    [60, "1m"],
    [2700, "45m"],
    [3900, "1h 05m"],
    [43200, "12h 00m"],
    [3569, "59m"],
    [3570, "1h 00m"],
  ])("%s -> %s", (s, out) => expect(fmtDuration(s)).toBe(out));
});

describe("hourLabel", () => {
  it("formats 12-hour labels", () => {
    expect(hourLabel(0)).toBe("12am");
    expect(hourLabel(1)).toBe("1am");
    expect(hourLabel(12)).toBe("12pm");
    expect(hourLabel(13)).toBe("1pm");
    expect(hourLabel(23)).toBe("11pm");
  });
});

describe("palette / fmtDay", () => {
  it("has 8 distinct colors plus gray other", () => {
    const cols = new Set(Array.from({ length: 8 }, (_, i) => palette(i)));
    expect(cols.size).toBe(8);
    expect(palette(-1)).not.toBe(palette(0));
  });
  it("fmtDay short weekday + day", () => {
    expect(fmtDay("2026-11-04")).toMatch(/^Wed 4$/);
  });
});
