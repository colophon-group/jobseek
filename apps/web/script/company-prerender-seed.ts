import fs from "node:fs";
import path from "node:path";
import { parse } from "csv-parse/sync";

/** Build-time only: one stable seed enables on-demand company route upgrades. */
export function companyPrerenderSeed(webRoot: string): string {
  const rows = parse(
    fs.readFileSync(path.resolve(webRoot, "../crawler/data/companies.csv"), "utf8"),
    { columns: true, skip_empty_lines: true },
  ) as { slug?: string }[];
  const slugs = rows.map((row) => row.slug?.trim() ?? "");
  if (
    slugs.length === 0 ||
    slugs.some((slug) => !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug))
  ) {
    throw new Error("Company prerender seed requires a nonempty canonical company registry");
  }
  // Registry ordering must not change which four localized pages are built.
  // Never return the full registry: the long tail is generated on demand.
  return slugs.sort()[0];
}
