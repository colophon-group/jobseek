import { describe, expect, it } from "vitest";
import {
  companyOgCacheKeyForVersion,
  companyOgPublicUrl,
} from "../company-og-key";
import { computeCompanyOgRendererVersion } from "../company-og-renderer-version";

describe("company OG renderer namespace", () => {
  it("is deterministic and changes when the explicit salt changes", () => {
    const root = process.cwd();
    const first = computeCompanyOgRendererVersion(root, "");
    const second = computeCompanyOgRendererVersion(root, "");
    const salted = computeCompanyOgRendererVersion(root, "rerender-canary");

    expect(first).toMatch(/^[a-f0-9]{16}$/);
    expect(second).toBe(first);
    expect(salted).not.toBe(first);
  });

  it("normalizes namespace segments identically for R2 keys and public URLs", () => {
    const key = companyOgCacheKeyForVersion("Renderer 123", "EN", "Acme, Inc.");
    expect(key).toBe("og/company/renderer-123/en/acme-inc.png");
    expect(
      companyOgPublicUrl(
        "https://assets.example.test",
        "Renderer 123",
        "EN",
        "Acme, Inc.",
      ),
    ).toBe("https://assets.example.test/og/company/renderer-123/en/acme-inc.png");
  });

  it("rejects non-HTTPS public object domains", () => {
    expect(companyOgPublicUrl("http://assets.example.test", "v1", "en", "acme"))
      .toBeNull();
  });
});
