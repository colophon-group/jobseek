import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "@/test-utils/lingui-mock";
import { WatchlistRuntimeFallback } from "./watchlist-runtime-fallback";

describe("WatchlistRuntimeFallback", () => {
  it("exposes a visible, polite busy status while viewer data resolves", () => {
    render(<WatchlistRuntimeFallback locale="en" />);

    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toContain("Loading…");
  });

  it("renders locale-safe copy without an RSC i18n context", () => {
    const { rerender } = render(<WatchlistRuntimeFallback locale="de" />);
    expect(screen.getByRole("status").textContent).toContain("Laden…");

    rerender(<WatchlistRuntimeFallback locale="fr" />);
    expect(screen.getByRole("status").textContent).toContain("Chargement…");

    rerender(<WatchlistRuntimeFallback locale="it" />);
    expect(screen.getByRole("status").textContent).toContain("Caricamento…");

    const source = readFileSync(
      join(__dirname, "watchlist-runtime-fallback.tsx"),
      "utf8",
    );
    expect(source).not.toContain("@lingui");
    expect(source).not.toContain("<Trans");
  });

  it("guards the session-aware route boundary from an empty fallback", () => {
    const source = readFileSync(join(__dirname, "page.tsx"), "utf8");

    expect(source).toContain(
      "<Suspense fallback={<WatchlistRuntimeFallback locale={locale} />}>",
    );
    expect(source).not.toContain("<Suspense fallback={null}>");
  });
});
