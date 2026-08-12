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
    expect(appLayout).toContain("const currencyRates = await getCurrencyRates();");
    expect(appLayout).toContain(
      "<AppBootstrapProvider initialCurrencyRates={currencyRates}>",
    );
    expect(bootstrapProvider).toContain("initialCurrencyRates: CurrencyRate[];");
    expect(bootstrapProvider).toContain("initialRates={initialCurrencyRates}");
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
