#!/usr/bin/env node
import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { basename } from "node:path";

const DEFAULT_SITEMAP_URL = "https://dealroom.co/lists/sitemap.xml";
const DEFAULT_COMPANIES_CSV = "apps/crawler/data/companies.csv";
const DEFAULT_PARENT_ISSUE = "3570";
const USER_AGENT =
  "jobseek-dealroom-company-requests/1.0 (+https://github.com/colophon-group/jobseek/issues/3570)";

export function slugify(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/\+/g, "plus")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-|-$/g, "");
}

export function normalizeHost(rawUrl) {
  if (!rawUrl) return "";
  const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(rawUrl) ? rawUrl : `https://${rawUrl}`;
  let host;
  try {
    host = new URL(withScheme).hostname.toLowerCase();
  } catch {
    return "";
  }
  host = host.replace(/\.$/, "");
  while (/^(www\d*|m)\./.test(host)) {
    host = host.replace(/^(www\d*|m)\./, "");
  }
  return host;
}

export function normalizeName(value) {
  let normalized = slugify(value);
  const suffixes = [
    "bank",
    "technologies",
    "technology",
    "labs",
    "lab",
    "systems",
    "system",
    "inc",
    "co",
    "company",
    "group",
    "gmbh",
    "ltd",
    "limited",
    "plc",
    "ag",
    "ai",
  ];
  let changed = true;
  while (changed) {
    changed = false;
    for (const suffix of suffixes) {
      const tail = `-${suffix}`;
      if (normalized.endsWith(tail) && normalized.length > tail.length + 2) {
        normalized = normalized.slice(0, -tail.length);
        changed = true;
      }
    }
  }
  return normalized;
}

export function parseCsvRows(source) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    const next = source[i + 1];

    if (inQuotes) {
      if (char === '"' && next === '"') {
        field += '"';
        i += 1;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  if (rows.length === 0) return [];
  const [header, ...body] = rows;
  return body
    .filter((cells) => cells.some((cell) => cell !== ""))
    .map((cells) => Object.fromEntries(header.map((name, index) => [name, cells[index] ?? ""])));
}

export function parseListSitemapXml(source) {
  return [...source.matchAll(/<loc>\s*([^<]+?)\s*<\/loc>/g)].map((match) =>
    decodeXmlEntities(match[1].trim()),
  );
}

export function extractDealroomCompaniesFromHtml(html, listUrl) {
  const scripts = [
    ...html.matchAll(
      /<script\b[^>]*type=(["'])application\/ld\+json\1[^>]*>([\s\S]*?)<\/script>/gi,
    ),
  ].map((match) => decodeHtmlEntities(match[2].trim()));

  let itemList;
  for (const script of scripts) {
    const data = JSON.parse(script);
    if (data && data["@type"] === "ItemList" && Array.isArray(data.itemListElement)) {
      itemList = data;
      break;
    }
  }

  if (!itemList) {
    throw new Error(`No Schema.org ItemList JSON-LD found in ${listUrl}`);
  }

  const listTitle = itemList.name || listTitleFromUrl(listUrl);
  return itemList.itemListElement.map((element) => {
    const item = element.item ?? {};
    const name = String(item.name ?? "").trim();
    const website = String(item.url ?? "").trim();
    return {
      name,
      website,
      host: normalizeHost(website),
      slug: slugify(name),
      nameKey: normalizeName(name),
      listTitle,
      listUrl,
      position: Number(element.position),
    };
  });
}

export function buildRegistryIndex(companyRows) {
  const hosts = new Map();
  const slugs = new Set();
  const names = new Map();

  for (const row of companyRows) {
    const host = normalizeHost(row.website);
    if (host && !hosts.has(host)) hosts.set(host, row);
    if (row.slug) slugs.add(row.slug);

    const nameKey = normalizeName(row.name);
    if (nameKey && !names.has(nameKey)) names.set(nameKey, row);

    const slugKey = normalizeName(row.slug);
    if (slugKey && !names.has(slugKey)) names.set(slugKey, row);
  }

  return { hosts, slugs, names };
}

export function auditDealroomEntries(entries, registryIndex) {
  const byIdentity = new Map();
  for (const entry of entries) {
    const key = entry.host || entry.slug;
    const current = byIdentity.get(key);
    if (current) {
      current.lists.push({
        title: entry.listTitle,
        url: entry.listUrl,
        position: entry.position,
      });
      continue;
    }
    byIdentity.set(key, {
      name: entry.name,
      website: entry.website,
      host: entry.host,
      slug: entry.slug,
      nameKey: entry.nameKey,
      lists: [{ title: entry.listTitle, url: entry.listUrl, position: entry.position }],
      match: null,
    });
  }

  const missing = [];
  const stats = {
    uniqueCompanies: byIdentity.size,
    matchedByHost: 0,
    matchedBySlug: 0,
    matchedByName: 0,
    missing: 0,
  };

  for (const company of byIdentity.values()) {
    if (company.host && registryIndex.hosts.has(company.host)) {
      company.match = { by: "host", row: registryIndex.hosts.get(company.host) };
      stats.matchedByHost += 1;
    } else if (registryIndex.slugs.has(company.slug)) {
      company.match = { by: "slug", slug: company.slug };
      stats.matchedBySlug += 1;
    } else if (registryIndex.names.has(company.nameKey)) {
      company.match = { by: "name", row: registryIndex.names.get(company.nameKey) };
      stats.matchedByName += 1;
    } else {
      missing.push(company);
    }
  }

  missing.sort((a, b) => {
    const listDelta = b.lists.length - a.lists.length;
    if (listDelta !== 0) return listDelta;
    return a.name.localeCompare(b.name, "en", { sensitivity: "base" });
  });
  stats.missing = missing.length;

  return { stats, missing, all: [...byIdentity.values()] };
}

export function buildIssueTitle(company) {
  return `Add company: ${company.name}`;
}

export function buildIssueBody(company, { parentIssue = DEFAULT_PARENT_ISSUE } = {}) {
  const listLines = company.lists
    .slice()
    .sort((a, b) => String(a.title).localeCompare(String(b.title), "en"))
    .map((list) => `- ${list.title} #${list.position}: ${list.url}`);

  return [
    "A user requested to add a company or fix an existing scraper.",
    "",
    "### User request",
    "",
    company.website || company.name,
    "",
    "### User context",
    "",
    `- **Source:** Dealroom top lists (#${parentIssue})`,
    `- **Company name:** ${company.name}`,
    company.website ? `- **Website:** ${company.website}` : null,
    `- **Dealroom slug candidate:** \`${company.slug}\``,
    "",
    "### Dealroom list evidence",
    "",
    ...listLines,
    "",
    `Parent tracking issue: #${parentIssue}`,
  ]
    .filter((line) => line !== null)
    .join("\n");
}

export function summarizeAudit({ stats, missing }) {
  const lines = [
    `Dealroom unique companies: ${stats.uniqueCompanies}`,
    `Matched by website host: ${stats.matchedByHost}`,
    `Matched by slug: ${stats.matchedBySlug}`,
    `Matched by normalized name: ${stats.matchedByName}`,
    `Missing unique companies: ${stats.missing}`,
  ];

  for (const company of missing.slice(0, 40)) {
    const memberships = company.lists
      .slice(0, 4)
      .map((list) => `${list.title} #${list.position}`)
      .join("; ");
    const suffix = company.lists.length > 4 ? `; +${company.lists.length - 4} more` : "";
    lines.push(`${company.lists.length}x\t${company.name}\t${company.website}\t${memberships}${suffix}`);
  }

  return `${lines.join("\n")}\n`;
}

export async function fetchDealroomEntries({
  sitemapUrl = DEFAULT_SITEMAP_URL,
  fetchImpl = globalThis.fetch,
} = {}) {
  const sitemap = await fetchText(fetchImpl, sitemapUrl);
  const urls = parseListSitemapXml(sitemap);
  const entries = [];
  for (const url of urls) {
    const html = await fetchText(fetchImpl, url);
    entries.push(...extractDealroomCompaniesFromHtml(html, url));
  }
  return { urls, entries };
}

async function fetchText(fetchImpl, url) {
  const response = await fetchImpl(url, {
    headers: {
      "User-Agent": USER_AGENT,
      Accept: "text/html,application/xml,application/json;q=0.9,*/*;q=0.8",
    },
  });
  if (!response.ok) {
    throw new Error(`GET ${url} failed with ${response.status}`);
  }
  return response.text();
}

function decodeXmlEntities(value) {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function decodeHtmlEntities(value) {
  return decodeXmlEntities(value);
}

function listTitleFromUrl(url) {
  return basename(url.replace(/\/$/, "")).replace(/-/g, " ");
}

function parseArgs(argv) {
  const args = {
    companies: DEFAULT_COMPANIES_CSV,
    sitemap: DEFAULT_SITEMAP_URL,
    parentIssue: DEFAULT_PARENT_ISSUE,
    limit: 40,
    delayMs: 1000,
    createIssues: false,
    yes: false,
    json: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--companies") args.companies = argv[++i];
    else if (arg === "--sitemap") args.sitemap = argv[++i];
    else if (arg === "--parent-issue") args.parentIssue = argv[++i];
    else if (arg === "--limit") args.limit = parseLimit(argv[++i]);
    else if (arg === "--delay-ms") args.delayMs = parseNonNegativeInteger(argv[++i], "--delay-ms");
    else if (arg === "--create-issues") args.createIssues = true;
    else if (arg === "--yes") args.yes = true;
    else if (arg === "--json") args.json = true;
    else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
}

function parseLimit(value) {
  if (value === "all") return Number.POSITIVE_INFINITY;
  const limit = Number.parseInt(value, 10);
  if (!Number.isFinite(limit) || limit < 0) {
    throw new Error("--limit must be a non-negative integer or 'all'");
  }
  return limit;
}

function printHelp() {
  console.log(`Usage: node scripts/dealroom-company-requests.mjs [options]

Audits Dealroom public top-list companies against apps/crawler/data/companies.csv.
By default this is read-only and prints the highest-priority missing companies.

Options:
  --companies <path>       Company registry CSV (default: ${DEFAULT_COMPANIES_CSV})
  --sitemap <url>          Dealroom lists sitemap (default: ${DEFAULT_SITEMAP_URL})
  --parent-issue <number>  Parent tracking issue (default: ${DEFAULT_PARENT_ISSUE})
  --limit <n|all>          Missing companies to print/create (default: 40)
  --delay-ms <n>           Delay between issue creates (default: 1000)
  --json                   Print JSON instead of text summary
  --create-issues          Create GitHub company-request issues for missing companies
  --yes                    Required with --create-issues
`);
}

function fetchExistingCompanyRequestIssues() {
  const stdout = execFileSync(
    "gh",
    [
      "api",
      "--method",
      "GET",
      "--paginate",
      "repos/colophon-group/jobseek/issues",
      "-f",
      "labels=company-request",
      "-f",
      "state=all",
      "-F",
      "per_page=100",
      "--jq",
      ".[] | {number,title}",
    ],
    { encoding: "utf8" },
  );

  const issues = new Map();
  for (const line of stdout.split("\n").filter(Boolean)) {
    const issue = JSON.parse(line);
    issues.set(issue.title.toLowerCase(), issue.number);
  }
  return issues;
}

function createIssue(title, body) {
  const result = spawnSync(
    "gh",
    ["issue", "create", "--title", title, "--body", body, "--label", "company-request"],
    { encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `gh issue create failed for ${title}`);
  }
  return result.stdout.trim();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.createIssues && !args.yes) {
    throw new Error("--create-issues requires --yes");
  }

  const companyRows = parseCsvRows(readFileSync(args.companies, "utf8"));
  const registry = buildRegistryIndex(companyRows);
  const { urls, entries } = await fetchDealroomEntries({ sitemapUrl: args.sitemap });
  const audit = auditDealroomEntries(entries, registry);
  const limitedMissing = audit.missing.slice(0, args.limit);

  if (args.json) {
    console.log(
      JSON.stringify(
        {
          source: { sitemapUrl: args.sitemap, listCount: urls.length },
          stats: audit.stats,
          missing: limitedMissing,
        },
        null,
        2,
      ),
    );
  } else {
    process.stdout.write(summarizeAudit({ stats: audit.stats, missing: limitedMissing }));
  }

  if (!args.createIssues) return;

  const existing = fetchExistingCompanyRequestIssues();
  for (const company of limitedMissing) {
    const title = buildIssueTitle(company);
    const existingNumber = existing.get(title.toLowerCase());
    if (existingNumber) {
      console.log(`SKIP existing #${existingNumber}: ${title}`);
      continue;
    }

    const url = createIssue(title, buildIssueBody(company, { parentIssue: args.parentIssue }));
    console.log(`CREATED ${title}: ${url}`);
    if (args.delayMs > 0) {
      await sleep(args.delayMs);
    }
  }
}

function parseNonNegativeInteger(value, flag) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${flag} must be a non-negative integer`);
  }
  return parsed;
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
