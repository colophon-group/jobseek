import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const COMPANY_OG_SOURCE_HASH_VERSION = "company-og-source-v1";

const COMPANY_OG_SOURCE_INPUTS = [
  "../crawler/data/companies.csv",
  "../crawler/data/company_descriptions.csv",
  "../crawler/data/industries.csv",
] as const;

/** Hash the versioned company data that can alter a rendered company card. */
export function computeCompanyOgSourceVersion(rootDir: string): string {
  const hash = crypto.createHash("sha256");
  hash.update(`${COMPANY_OG_SOURCE_HASH_VERSION}\n`);

  for (const inputPath of COMPANY_OG_SOURCE_INPUTS) {
    const absolute = path.resolve(rootDir, inputPath);
    if (!fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
      throw new Error(`Company OG source is missing: ${inputPath}`);
    }
    hash.update(inputPath);
    hash.update("\0");
    hash.update(fs.readFileSync(absolute));
    hash.update("\0");
  }

  return hash.digest("hex").slice(0, 16);
}
