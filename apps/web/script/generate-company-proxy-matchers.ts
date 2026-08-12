import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { parse } from "csv-parse/sync";

const GENERATED_START = "    // BEGIN GENERATED COMPANY MISS MATCHERS";
const GENERATED_END = "    // END GENERATED COMPANY MISS MATCHERS";
const SAFE_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

type CompanyRow = { slug?: string };
type MatcherPartition = {
  knownSlugs: string[];
  slugPattern: string;
};

const MAX_SOURCE_LENGTH = 3_500;
const MAX_LEAF_SOURCE_LENGTH = 2_400;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function companyMissMatcher(partitions: readonly MatcherPartition[]): string {
  const knownSlugs = partitions.flatMap(({ knownSlugs }) => knownSlugs).sort();
  const knownGuard = knownSlugs.length > 0
    ? `(?!(?:${knownSlugs.join("|")})(?:/|$))`
    : "";
  const slugPatterns = partitions.map(({ slugPattern }) => slugPattern).join("|");
  return `/:lang(en|de|fr|it)/company/:slug(${knownGuard}(?:${slugPatterns}))`;
}

function partitionSlugs(prefix: string, slugs: readonly string[]): MatcherPartition[] {
  const leaf: MatcherPartition = {
    knownSlugs: [...slugs],
    slugPattern: `${escapeRegExp(prefix)}[^/]*`,
  };
  if (companyMissMatcher([leaf]).length <= MAX_LEAF_SOURCE_LENGTH) {
    return [leaf];
  }

  const terminalSlugs: string[] = [];
  const byNextCharacter = new Map<string, string[]>();
  for (const slug of slugs) {
    const next = slug[prefix.length];
    if (!next) {
      terminalSlugs.push(slug);
      continue;
    }
    const group = byNextCharacter.get(next) ?? [];
    group.push(slug);
    byNextCharacter.set(next, group);
  }

  const partitions = [...byNextCharacter]
    .sort(([a], [b]) => a.localeCompare(b))
    .flatMap(([next, group]) => partitionSlugs(`${prefix}${next}`, group));
  const knownNextCharacters = [...byNextCharacter.keys()]
    .sort()
    .map(escapeRegExp)
    .join("|");
  partitions.push({
    knownSlugs: terminalSlugs,
    slugPattern: `${escapeRegExp(prefix)}(?!(?:${knownNextCharacters}))[^/]*`,
  });
  return partitions;
}

function packPartitions(partitions: readonly MatcherPartition[]): MatcherPartition[][] {
  const groups: MatcherPartition[][] = [];
  const largestFirst = [...partitions].sort(
    (a, b) => companyMissMatcher([b]).length - companyMissMatcher([a]).length,
  );
  for (const partition of largestFirst) {
    const group = groups.find(
      (candidate) =>
        companyMissMatcher([...candidate, partition]).length <= MAX_SOURCE_LENGTH,
    );
    if (group) {
      group.push(partition);
    } else {
      if (companyMissMatcher([partition]).length > MAX_SOURCE_LENGTH) {
        throw new Error(`Company matcher partition is too large: ${partition.slugPattern}`);
      }
      groups.push([partition]);
    }
  }
  return groups;
}

function buildMatcherBlock(slugs: readonly string[]): string {
  const byFirstCharacter = new Map<string, string[]>();
  for (const slug of slugs) {
    const first = slug[0];
    if (!first) throw new Error("Company registry contains an empty slug");
    const group = byFirstCharacter.get(first) ?? [];
    group.push(slug);
    byFirstCharacter.set(first, group);
  }

  const lines = [
    GENERATED_START,
    "    // Generated from apps/crawler/data/companies.csv. Canonical company",
    "    // documents bypass Proxy so a warm page-cache hit consumes no Fluid",
    "    // middleware compute. Only absent/unsafe slug candidates reach the",
    "    // Typesense-backed real-404 guard below. Run `pnpm proxy-matchers:update`.",
  ];

  const partitions = [...byFirstCharacter]
    .sort(([a], [b]) => a.localeCompare(b))
    .flatMap(([first, group]) => partitionSlugs(first, group.sort()));
  const knownInitials = [...byFirstCharacter.keys()].sort().join("");
  partitions.push({
    knownSlugs: [],
    slugPattern: `(?![${knownInitials}])[^/]+`,
  });

  // Next caps an individual built matcher at 4096 characters. Pack the
  // disjoint prefix partitions under a conservative source limit so the
  // generated client manifest remains compact without crossing that cap.
  for (const group of packPartitions(partitions)) {
    lines.push(`    ${JSON.stringify(companyMissMatcher(group))},`);
  }
  lines.push(GENERATED_END);
  return lines.join("\n");
}

function main(): void {
  const webRoot = process.cwd();
  const registryPath = path.resolve(webRoot, "../crawler/data/companies.csv");
  const proxyPath = path.resolve(webRoot, "proxy.ts");
  const rows = parse(fs.readFileSync(registryPath, "utf8"), {
    columns: true,
    skip_empty_lines: true,
  }) as CompanyRow[];
  const slugs = rows.map(({ slug }) => slug?.trim() ?? "");
  const invalid = slugs.filter((slug) => !SAFE_SLUG.test(slug));
  if (invalid.length > 0) {
    throw new Error(`Company registry contains invalid slugs: ${invalid.slice(0, 5).join(", ")}`);
  }
  if (new Set(slugs).size !== slugs.length) {
    throw new Error("Company registry contains duplicate slugs");
  }

  const source = fs.readFileSync(proxyPath, "utf8");
  const start = source.indexOf(GENERATED_START);
  const end = source.indexOf(GENERATED_END);
  if (start < 0 || end < start) {
    throw new Error("proxy.ts is missing generated company matcher markers");
  }
  const expected = [
    source.slice(0, start),
    buildMatcherBlock(slugs),
    source.slice(end + GENERATED_END.length),
  ].join("");

  if (source === expected) {
    console.log(`[proxy-matchers] verified ${slugs.length} company exclusions`);
    return;
  }
  if (process.argv.includes("--write")) {
    fs.writeFileSync(proxyPath, expected);
    console.log(`[proxy-matchers] wrote ${slugs.length} company exclusions`);
    return;
  }

  console.error(
    "[proxy-matchers] proxy.ts is stale; run `pnpm proxy-matchers:update` from apps/web",
  );
  process.exitCode = 1;
}

main();
