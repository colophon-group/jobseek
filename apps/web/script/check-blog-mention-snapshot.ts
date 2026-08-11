/**
 * Validate/regenerate the repository-owned MDX mention snapshot.
 *
 * Default mode is a CI/build gate. `--write` is the explicit author action
 * after adding a company mention or changing one of the approved CSV rows.
 * Watchlist metadata is editorial and is therefore preserved from the
 * reviewed snapshot; a newly mentioned watchlist must be added there first.
 */
import { existsSync } from "node:fs";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "csv-parse/sync";
import ts from "typescript";

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BLOG_DIR = resolve(WEB_ROOT, "src/content/blog");
const SNAPSHOT_PATH = resolve(BLOG_DIR, "mention-snapshot.json");
const COMPANY_DATA_DIR = resolve(WEB_ROOT, "../crawler/data");
const MENTION_COMPONENT_PATH = resolve(
  WEB_ROOT,
  "src/components/blog/MdxMentions.tsx",
);
const LOCALES = ["en", "de", "fr", "it"] as const;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/u;
const EXTERNAL_CALL_BUDGET = 0;

type CsvRow = Record<string, string>;
type CompanySnapshot = {
  slug: string;
  name: string;
  icon: string | null;
  industryName: string | null;
  employeeCountRange: number | null;
  foundedYear: number | null;
  descriptions: Record<(typeof LOCALES)[number], string | null>;
};
type WatchlistSnapshot = {
  owner: string;
  ownerLabel: string;
  slug: string;
  title: string;
  description: string | null;
  companyCount: number | null;
};
type MentionSnapshot = {
  schemaVersion: 1;
  companies: CompanySnapshot[];
  watchlists: WatchlistSnapshot[];
};
type MentionRefs = {
  companies: Set<string>;
  watchlists: Set<string>;
};

function csv(source: string, label: string): CsvRow[] {
  const rows = parse(source, {
    bom: true,
    columns: true,
    relax_column_count: false,
    skip_empty_lines: true,
  }) as CsvRow[];
  if (rows.length === 0) throw new Error(`${label} must not be empty`);
  return rows;
}

function value(row: CsvRow, field: string): string | null {
  const candidate = row[field]?.trim();
  return candidate ? candidate : null;
}

function optionalInteger(row: CsvRow, field: string, context: string): number | null {
  const candidate = value(row, field);
  if (candidate === null) return null;
  const parsed = Number.parseInt(candidate, 10);
  if (!Number.isSafeInteger(parsed) || String(parsed) !== candidate) {
    throw new Error(`${context}.${field} must be an integer`);
  }
  return parsed;
}

function uniqueRows(rows: CsvRow[], key: string, label: string): Map<string, CsvRow> {
  const result = new Map<string, CsvRow>();
  for (const row of rows) {
    const id = value(row, key);
    if (!id) throw new Error(`${label} row is missing ${key}`);
    if (result.has(id)) throw new Error(`${label} has duplicate ${key}: ${id}`);
    result.set(id, row);
  }
  return result;
}

function attributes(source: string): Map<string, string> {
  const result = new Map<string, string>();
  for (const match of source.matchAll(/([A-Za-z][\w-]*)\s*=\s*(["'])(.*?)\2/gu)) {
    result.set(match[1], match[3]);
  }
  return result;
}

export function collectMentionRefs(sources: readonly string[]): MentionRefs {
  const refs: MentionRefs = { companies: new Set(), watchlists: new Set() };
  const tag = /<(Company|CompanyCard|Watchlist|WatchlistCard)\b([^>]*)\/?\s*>/gu;
  for (const source of sources) {
    for (const match of source.matchAll(tag)) {
      const name = match[1];
      const attrs = attributes(match[2]);
      const slug = attrs.get("slug");
      if (!slug || !SLUG.test(slug)) {
        throw new Error(`<${name}> must have a canonical literal slug`);
      }
      if (name === "Company" || name === "CompanyCard") {
        refs.companies.add(slug);
        continue;
      }
      const owner = attrs.get("owner");
      if (!owner || !SLUG.test(owner)) {
        throw new Error(`<${name}> must have a canonical literal owner`);
      }
      refs.watchlists.add(`${owner}/${slug}`);
    }
  }
  return refs;
}

function compareKeys(actual: readonly string[], expected: Set<string>, label: string): void {
  const duplicates = actual.filter((key, index) => actual.indexOf(key) !== index);
  if (duplicates.length > 0) {
    throw new Error(`${label} snapshot contains duplicates: ${[...new Set(duplicates)].join(", ")}`);
  }
  const actualSet = new Set(actual);
  const missing = [...expected].filter((key) => !actualSet.has(key));
  const stale = [...actualSet].filter((key) => !expected.has(key));
  if (missing.length || stale.length) {
    throw new Error(
      `${label} snapshot coverage mismatch; missing=[${missing.join(", ")}], stale=[${stale.join(", ")}]`,
    );
  }
}

export function buildExpectedSnapshot(
  refs: MentionRefs,
  companiesSource: string,
  descriptionsSource: string,
  industriesSource: string,
  current: MentionSnapshot,
): MentionSnapshot {
  const companies = uniqueRows(csv(companiesSource, "companies.csv"), "slug", "companies.csv");
  const descriptions = uniqueRows(
    csv(descriptionsSource, "company_descriptions.csv"),
    "slug",
    "company_descriptions.csv",
  );
  const industries = uniqueRows(csv(industriesSource, "industries.csv"), "id", "industries.csv");

  compareKeys(
    current.watchlists.map((watchlist) => `${watchlist.owner}/${watchlist.slug}`),
    refs.watchlists,
    "watchlist",
  );

  const expectedCompanies = [...refs.companies].sort().map((slug): CompanySnapshot => {
    const row = companies.get(slug);
    if (!row) throw new Error(`Company mention is absent from companies.csv: ${slug}`);
    const name = value(row, "name");
    if (!name) throw new Error(`Company mention has no name in companies.csv: ${slug}`);
    const industryId = value(row, "industry");
    const descriptionRow = descriptions.get(slug);
    return {
      slug,
      name,
      icon: value(row, "icon_url"),
      industryName: industryId ? value(industries.get(industryId) ?? {}, "name") : null,
      employeeCountRange: optionalInteger(row, "employee_count_range", slug),
      foundedYear: optionalInteger(row, "founded_year", slug),
      descriptions: Object.fromEntries(
        LOCALES.map((locale) => [
          locale,
          descriptionRow ? value(descriptionRow, locale) : null,
        ]),
      ) as CompanySnapshot["descriptions"],
    };
  });

  const expectedWatchlists = [...current.watchlists]
    .sort((a, b) => `${a.owner}/${a.slug}`.localeCompare(`${b.owner}/${b.slug}`));

  return {
    schemaVersion: 1,
    companies: expectedCompanies,
    watchlists: expectedWatchlists,
  };
}

function stableJson(snapshot: MentionSnapshot): string {
  return `${JSON.stringify(snapshot, null, 2)}\n`;
}

function localImportPath(fromPath: string, specifier: string): string | null {
  let base: string;
  if (specifier.startsWith("@/")) {
    base = resolve(WEB_ROOT, "src", specifier.slice(2));
  } else if (specifier.startsWith(".")) {
    base = resolve(dirname(fromPath), specifier);
  } else {
    return null;
  }
  if (extname(base)) return base;
  for (const extension of [".ts", ".tsx", ".json"]) {
    const candidate = `${base}${extension}`;
    if (existsSync(candidate)) return candidate;
  }
  return base;
}

const FORBIDDEN_LOCAL_BOUNDARIES = [
  "/src/db/",
  "/src/lib/actions/",
  "/src/lib/search/",
  "/src/lib/services/",
] as const;
const ALLOWED_EXTERNAL_PACKAGES = new Set([
  "@jseek/mcp-server/public-api-contract",
  "@lingui/core",
  "@lingui/react/server",
  "lucide-react",
  "next/image",
  "next/link",
  "react",
]);

/** Ensure the entire local import graph reachable from MDX mentions is offline. */
export async function assertOfflineImportGraph(entryPath: string): Promise<void> {
  const pending = [entryPath];
  const visited = new Set<string>();

  while (pending.length > 0) {
    const path = pending.pop()!;
    if (visited.has(path) || path.endsWith(".json")) continue;
    visited.add(path);
    if (FORBIDDEN_LOCAL_BOUNDARIES.some((segment) => path.includes(segment))) {
      throw new Error(`Blog mention import graph crosses external-data boundary: ${path}`);
    }

    const source = await readFile(path, "utf8");
    const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true);
    const visit = (node: ts.Node): void => {
      if (
        ts.isCallExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === "fetch"
      ) {
        throw new Error(`Blog mention import graph calls fetch(): ${path}`);
      }

      let specifier: string | null = null;
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
        specifier = node.moduleSpecifier.text;
      } else if (
        ts.isExportDeclaration(node) &&
        node.moduleSpecifier &&
        ts.isStringLiteral(node.moduleSpecifier)
      ) {
        specifier = node.moduleSpecifier.text;
      }
      if (specifier) {
        const local = localImportPath(path, specifier);
        if (local) {
          pending.push(local);
        } else if (!ALLOWED_EXTERNAL_PACKAGES.has(specifier)) {
          throw new Error(`Blog mention import graph imports unapproved package ${specifier}: ${path}`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
}

async function main(): Promise<void> {
  const write = process.argv.slice(2).includes("--write");
  const filenames = (await readdir(BLOG_DIR)).filter((name) => name.endsWith(".mdx"));
  const sources = await Promise.all(
    filenames.map((name) => readFile(resolve(BLOG_DIR, name), "utf8")),
  );
  const refs = collectMentionRefs(sources);
  const current = JSON.parse(await readFile(SNAPSHOT_PATH, "utf8")) as MentionSnapshot;
  if (current.schemaVersion !== 1) throw new Error("Unsupported blog mention snapshot schema");

  const [companiesSource, descriptionsSource, industriesSource] = await Promise.all([
    readFile(resolve(COMPANY_DATA_DIR, "companies.csv"), "utf8"),
    readFile(resolve(COMPANY_DATA_DIR, "company_descriptions.csv"), "utf8"),
    readFile(resolve(COMPANY_DATA_DIR, "industries.csv"), "utf8"),
  ]);
  const expected = buildExpectedSnapshot(
    refs,
    companiesSource,
    descriptionsSource,
    industriesSource,
    current,
  );
  if (write) {
    await writeFile(SNAPSHOT_PATH, stableJson(expected), "utf8");
  } else {
    compareKeys(current.companies.map((company) => company.slug), refs.companies, "company");
    if (stableJson(current) !== stableJson(expected)) {
      throw new Error(
        "Blog mention snapshot is stale. Run `pnpm blog-mentions:update` and review the diff.",
      );
    }
  }

  await assertOfflineImportGraph(MENTION_COMPONENT_PATH);
  console.log(
    `[blog-mentions] ${refs.companies.size + refs.watchlists.size} unique entities across ${LOCALES.length} locales; external-call budget=${EXTERNAL_CALL_BUDGET}`,
  );
}

const entrypoint = process.argv[1] ?? "";
if (/check-blog-mention-snapshot\.(?:ts|js|mjs|cjs)$/u.test(entrypoint)) {
  void main().catch(() => {
    // Never echo an error-derived value: CI/build environments can contain
    // client configuration and credentials that do not belong in logs.
    console.error("Blog mention snapshot validation failed");
    process.exitCode = 1;
  });
}
