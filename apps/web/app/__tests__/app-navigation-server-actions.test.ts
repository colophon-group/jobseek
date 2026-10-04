import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appLayout = readFileSync("app/[lang]/(app)/layout.tsx", "utf8");
const bootstrapProvider = readFileSync(
  "src/components/providers/AppBootstrapProvider.tsx",
  "utf8",
);
const salaryProvider = readFileSync(
  "src/components/providers/SalaryDisplayProvider.tsx",
  "utf8",
);

describe("anonymous app navigation Server Action contract (#2640)", () => {
  it("preloads viewer-independent currency rates through the cached server service", () => {
    expect(appLayout).toContain(
      'import { getCurrencyRates } from "@/lib/services/search";',
    );
    expect(appLayout).toContain("return getCurrencyRates();");
    expect(appLayout).toContain("const currencyRates = await getDisplayCurrencySnapshot();");
    expect(appLayout).toContain("initialCurrencyRates={currencyRates}");
    expect(bootstrapProvider).toContain("initialCurrencyRates: CurrencyRate[];");
    expect(bootstrapProvider).toContain("initialRates={initialCurrencyRates}");
  });

  it("keeps the shared app layout cache-safe and resolves the login hint in the browser", () => {
    expect(appLayout).not.toContain('from "next/headers"');
    expect(appLayout).not.toMatch(
      /from\s+["']@\/lib\/(?:actions\/bootstrap|client-cookies|sessionCache)["']/u,
    );
    expect(appLayout).not.toMatch(
      /\b(?:cookies|headers|fetchAppBootstrap|hasLoggedInHint|readCookieValue|getSession(?:UserId)?)\s*\(/u,
    );
    expect(bootstrapProvider).toMatch(/^["']use client["'];/u);
    expect(bootstrapProvider).not.toContain('from "next/headers"');
    expect(bootstrapProvider).toContain("hasLoggedInHint()");
    // AppBootstrapProvider.test.tsx verifies the mounted absent-hint zero-RPC
    // behavior, bounded timeout/manual retry, Strict Mode replay, and stale
    // identity races. Timer spelling does not establish this layout contract.
  });

  it("does not import or invoke a Server Action from SalaryDisplayProvider on mount", () => {
    expect(salaryProvider).not.toMatch(
      /import\s+\{[^}]*getCurrencyRates[^}]*\}\s+from\s+["']@\/lib\/actions\/search["']/u,
    );
    expect(salaryProvider).not.toMatch(
      /useEffect\(\(\)\s*=>\s*\{[^}]*getCurrencyRates\(/u,
    );
    expect(salaryProvider).toContain("const rates = initialRates;");
  });
});
