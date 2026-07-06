import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("next/og", () => ({
  ImageResponse: class extends Response {
    constructor(_element: unknown, init: ResponseInit = {}) {
      super(new Uint8Array([9, 8, 7]), init);
    }
  },
}));

vi.mock("@/lib/actions/company", () => ({
  getCompanyBySlug: vi.fn(),
}));

vi.mock("@/lib/og/company-og-cache", () => ({
  companyOgCacheKey: vi.fn(() => "og/company/test/en/acme.png"),
  readCompanyOgCache: vi.fn(),
  shouldBypassCompanyOgCache: vi.fn(() => false),
  writeCompanyOgCache: vi.fn(),
}));

import * as ogImage from "../opengraph-image";

describe("company opengraph image route rendering mode", () => {
  it("is dynamic because request-time R2 cache IO is intentional", () => {
    expect(ogImage.dynamic).toBe("force-dynamic");
    expect("generateStaticParams" in ogImage).toBe(false);
  });
});
