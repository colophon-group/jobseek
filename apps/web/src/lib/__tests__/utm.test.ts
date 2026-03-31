import { describe, it, expect } from "vitest";
import { withUtmSource } from "@/lib/utm";

describe("withUtmSource", () => {
  it("appends utm_source to http URLs", () => {
    expect(withUtmSource("https://example.com/jobs/123")).toBe(
      "https://example.com/jobs/123?utm_source=jobseek",
    );
  });

  it("preserves existing utm_source", () => {
    expect(withUtmSource("https://example.com?utm_source=other")).toBe(
      "https://example.com/?utm_source=other",
    );
  });

  it("preserves existing query params", () => {
    const result = withUtmSource("https://example.com?foo=bar");
    expect(result).toContain("foo=bar");
    expect(result).toContain("utm_source=jobseek");
  });

  it("blocks javascript: scheme", () => {
    expect(withUtmSource("javascript:alert(1)")).toBe("#");
  });

  it("blocks data: scheme", () => {
    expect(withUtmSource("data:text/html,<script>alert(1)</script>")).toBe("#");
  });

  it("blocks vbscript: scheme", () => {
    expect(withUtmSource("vbscript:msgbox")).toBe("#");
  });

  it("returns # for unparseable URLs", () => {
    expect(withUtmSource("not a url at all")).toBe("#");
  });

  it("allows http: scheme", () => {
    expect(withUtmSource("http://example.com")).toContain("http://example.com");
  });
});
