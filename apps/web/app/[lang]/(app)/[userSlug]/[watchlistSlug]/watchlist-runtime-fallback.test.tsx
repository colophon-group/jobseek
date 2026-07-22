import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "@/test-utils/lingui-mock";
import { WatchlistRuntimeFallback } from "./watchlist-runtime-fallback";

describe("WatchlistRuntimeFallback", () => {
  it("exposes a visible, polite busy status while viewer data resolves", () => {
    render(<WatchlistRuntimeFallback />);

    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toContain("Loading…");
  });

  it("guards the session-aware route boundary from an empty fallback", () => {
    const source = readFileSync(join(__dirname, "page.tsx"), "utf8");

    expect(source).toContain(
      "<Suspense fallback={<WatchlistRuntimeFallback />}>",
    );
    expect(source).not.toContain("<Suspense fallback={null}>");
  });
});
