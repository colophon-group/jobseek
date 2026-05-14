import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Regression guard for #3170 (WCAG 2.4.7 — Focus Visible).
 *
 * Tailwind v4 strips the browser-default focus outline. Before this fix
 * only ~5 components in the codebase declared their own `:focus-visible`
 * styling, leaving every other interactive element (the `Button`
 * primitive, icon buttons, filter chips, close buttons, watchlist cards,
 * locale + theme toggles, cookie banner, etc.) with no visible focus
 * indicator for keyboard users. The fix is a global `:focus-visible` rule
 * in `globals.css` scoped via `:where()` so per-component overrides keep
 * winning by specificity.
 *
 * Unit-asserting the compiled selector behaviour requires a browser; a
 * cheaper guard that catches the regression (the rule getting deleted
 * during a refactor) is to assert the literal CSS source still contains
 * the global selector + the `var(--primary)` outline.
 */
describe("globals.css :focus-visible (a11y, #3170)", () => {
  const cssPath = join(__dirname, "..", "globals.css");
  const css = readFileSync(cssPath, "utf8");

  it("declares a global :focus-visible rule", () => {
    expect(css).toMatch(/:focus-visible\s*\{/);
  });

  it("scopes the rule via :where() to keep specificity at 0", () => {
    // `:where()` selector wraps the element list so per-component focus
    // utilities (e.g. `focus:outline-none focus:ring-2` on the
    // agent-prompt-card copy buttons) still win without `!important`.
    expect(css).toMatch(/:where\([^)]*\):focus-visible/);
  });

  it("draws the outline from the --primary design token", () => {
    // Theme-aware: --primary inverts in `.dark` so the ring stays visible
    // in both light and dark mode without a second selector.
    const match = css.match(/:where\([^)]*\):focus-visible\s*\{[^}]*\}/);
    expect(match).not.toBeNull();
    expect(match![0]).toContain("outline");
    expect(match![0]).toContain("var(--primary)");
  });

  it("covers the primary interactive element types", () => {
    const match = css.match(/:where\(([^)]*)\):focus-visible/);
    expect(match).not.toBeNull();
    const selectorList = match![1];
    // Must cover plain links + buttons (the bulk of the codebase's
    // interactive elements) plus form fields and ARIA-roled custom widgets.
    for (const required of ["a", "button", "input", "[role=\"button\"]", "[tabindex]"]) {
      expect(selectorList).toContain(required);
    }
  });
});
