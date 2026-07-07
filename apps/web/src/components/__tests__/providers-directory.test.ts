import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const COMPONENTS_DIR = join(process.cwd(), "src/components");
const PROVIDERS_DIR = join(COMPONENTS_DIR, "providers");

const providerFiles = [
  "AppBootstrapProvider.tsx",
  "BannerProvider.tsx",
  "LinguiProvider.tsx",
  "PreferencesInitializer.tsx",
  "SalaryDisplayProvider.tsx",
  "SavedJobsProvider.tsx",
  "SearchStateProvider.tsx",
  "SessionProvider.tsx",
  "StarredCompaniesProvider.tsx",
];

describe("components provider directory", () => {
  it("keeps provider modules grouped under components/providers", () => {
    expect(existsSync(PROVIDERS_DIR)).toBe(true);
    expect(readdirSync(PROVIDERS_DIR).sort()).toEqual(providerFiles.sort());

    for (const file of providerFiles) {
      expect(existsSync(join(COMPONENTS_DIR, file))).toBe(false);
    }
  });
});
