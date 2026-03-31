import { describe, it, expect } from "vitest";
import { sanitizeJobHtml } from "@/lib/sanitize";

describe("sanitizeJobHtml", () => {
  it("preserves allowed semantic tags", () => {
    const html = "<p>Hello <strong>world</strong></p><ul><li>item</li></ul>";
    expect(sanitizeJobHtml(html)).toBe(html);
  });

  it("preserves heading tags", () => {
    const html = "<h1>Title</h1><h2>Subtitle</h2><h3>Section</h3>";
    expect(sanitizeJobHtml(html)).toBe(html);
  });

  it("strips script tags", () => {
    expect(sanitizeJobHtml('<p>ok</p><script>alert(1)</script>')).toBe(
      "<p>ok</p>",
    );
  });

  it("strips iframe tags", () => {
    expect(sanitizeJobHtml('<iframe src="evil.com"></iframe><p>ok</p>')).toBe(
      "<p>ok</p>",
    );
  });

  it("strips img tags (not in allowlist)", () => {
    expect(sanitizeJobHtml('<img src="x" onerror="alert(1)"><p>ok</p>')).toBe(
      "<p>ok</p>",
    );
  });

  it("strips div tags (not in allowlist)", () => {
    expect(sanitizeJobHtml("<div>content</div>")).toBe("content");
  });

  it("strips all attributes from allowed tags", () => {
    expect(sanitizeJobHtml('<p onclick="alert(1)">text</p>')).toBe(
      "<p>text</p>",
    );
  });

  it("strips href from anchor tags", () => {
    expect(
      sanitizeJobHtml('<a href="javascript:alert(1)">link</a>'),
    ).toBe("<a>link</a>");
  });

  it("strips style attributes", () => {
    expect(sanitizeJobHtml('<p style="color:red">text</p>')).toBe(
      "<p>text</p>",
    );
  });

  it("handles empty string", () => {
    expect(sanitizeJobHtml("")).toBe("");
  });
});
