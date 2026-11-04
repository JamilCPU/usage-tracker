import { describe, expect, it } from "vitest";
import { hourlyQuery } from "./api";
import { defaultFilters } from "./filters";

describe("hourlyQuery", () => {
  it("always requests all hours", () => {
    const f = { ...defaultFilters(new Date(2026, 10, 4)), hourFrom: 8, hourTo: 18 };
    const q = new URLSearchParams(hourlyQuery(f));
    expect(q.get("hour_from")).toBe("0");
    expect(q.get("hour_to")).toBe("23");
    expect(q.get("from")).toBe(f.from);
  });
});
