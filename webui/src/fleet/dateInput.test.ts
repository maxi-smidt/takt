import { describe, expect, it } from "vitest";
import { buildCalendarWeeks, formatDisplayDate, parseDisplayDate } from "./dateInput";

describe("formatDisplayDate", () => {
  it("converts an ISO date to dd.mm.yyyy", () => {
    expect(formatDisplayDate("2026-08-17")).toBe("17.08.2026");
  });

  it("returns an empty string for an empty value", () => {
    expect(formatDisplayDate("")).toBe("");
  });
});

describe("parseDisplayDate", () => {
  it("parses a valid dd.mm.yyyy date", () => {
    expect(parseDisplayDate("17.08.2026")).toBe("2026-08-17");
  });

  it("accepts single-digit day/month", () => {
    expect(parseDisplayDate("1.8.2026")).toBe("2026-08-01");
  });

  it("rejects a calendar date that does not exist", () => {
    expect(parseDisplayDate("31.02.2026")).toBeNull();
  });

  it("rejects malformed input", () => {
    expect(parseDisplayDate("2026-08-17")).toBeNull();
    expect(parseDisplayDate("not a date")).toBeNull();
  });
});

describe("buildCalendarWeeks", () => {
  it("starts every week on Monday", () => {
    const weeks = buildCalendarWeeks(2026, 7); // August 2026
    for (const week of weeks) {
      expect(week[0]?.getDay()).toBe(1);
      expect(week[6]?.getDay()).toBe(0);
    }
  });

  it("covers the full month including lead-in/lead-out days", () => {
    const weeks = buildCalendarWeeks(2026, 7); // August 2026 starts on a Saturday
    const allDays = weeks.flat();
    expect(allDays[0]?.toDateString()).toBe(new Date(2026, 6, 27).toDateString());
    expect(allDays.some((day) => day.getFullYear() === 2026 && day.getMonth() === 7 && day.getDate() === 1)).toBe(true);
    expect(allDays.some((day) => day.getFullYear() === 2026 && day.getMonth() === 7 && day.getDate() === 31)).toBe(true);
  });
});
