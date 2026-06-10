import { describe, expect, it } from "vitest";
import { extFromMime } from "./mime";

describe("extFromMime", () => {
  it("maps known image mime types", () => {
    expect(extFromMime("image/svg+xml")).toBe("svg");
    expect(extFromMime("image/png")).toBe("png");
    expect(extFromMime("image/jpeg")).toBe("jpg");
  });

  it("falls back to bin for unknown or missing mime", () => {
    expect(extFromMime("application/x-weird")).toBe("bin");
    expect(extFromMime(null)).toBe("bin");
    expect(extFromMime(undefined)).toBe("bin");
  });
});
