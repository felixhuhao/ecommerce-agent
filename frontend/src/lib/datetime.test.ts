import { describe, expect, it } from "vitest";
import { formatRelativeTime } from "./datetime";

describe("formatRelativeTime", () => {
  const now = Date.parse("2026-06-10T12:00:00Z");

  it("formats recent timestamps relatively", () => {
    expect(formatRelativeTime("2026-06-10T11:59:50Z", now)).toBe("just now");
    expect(formatRelativeTime("2026-06-10T11:30:00Z", now)).toBe("30m ago");
    expect(formatRelativeTime("2026-06-10T09:00:00Z", now)).toBe("3h ago");
    expect(formatRelativeTime("2026-06-08T12:00:00Z", now)).toBe("2d ago");
  });

  it("falls back to the raw value for invalid input", () => {
    expect(formatRelativeTime("not-a-date", now)).toBe("not-a-date");
  });
});
