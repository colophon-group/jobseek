/**
 * E2E tests for TypesenseSearchProvider.
 *
 * These tests seed synthetic data into a local Typesense instance, then
 * exercise every SearchProvider method against real Typesense queries.
 *
 * Requirements:
 *   - Typesense running at localhost:8108
 *   - API key: local_dev_typesense_key
 *
 * Run:
 *   pnpm vitest run src/lib/search/__tests__/typesense.e2e.test.ts
 *
 * @vitest-environment node
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { Client } from "typesense";
import { withTestEnvForAll } from "@/test-utils/env";
import type { CollectionCreateSchema } from "typesense/lib/Typesense/Collections";
import { TypesenseSearchProvider } from "../typesense";

// ── Constants ───────────────────────────────────────────────────────

const API_KEY = "local_dev_typesense_key";
const COLLECTION_PREFIX = "e2e_test_";
const JOB_POSTING_COLLECTION = `${COLLECTION_PREFIX}job_posting`;
const COMPANY_COLLECTION = `${COLLECTION_PREFIX}company`;

withTestEnvForAll({
  TYPESENSE_HOST: "localhost",
  TYPESENSE_PORT: "8108",
  TYPESENSE_PROTOCOL: "http",
  TYPESENSE_SEARCH_KEY: API_KEY,
});

// We use a direct admin client for seeding/cleanup. The provider uses the
// search client singleton (getSearchClient), which reads env vars lazily.
let adminClient: Client;
let provider: TypesenseSearchProvider;
let suiteSkipped = false;

function shouldRequireTypesenseE2E(): boolean {
  return process.env.CI === "true" || process.env.REQUIRE_TYPESENSE_E2E === "true";
}

// ── Seed data ───────────────────────────────────────────────────────

const NOW_UNIX = Math.floor(Date.now() / 1000);
const _ONE_YEAR_AGO = NOW_UNIX - 365 * 24 * 60 * 60;
const SIX_MONTHS_AGO = NOW_UNIX - 180 * 24 * 60 * 60;
const TWO_YEARS_AGO = NOW_UNIX - 730 * 24 * 60 * 60;

// 5 companies
const COMPANIES = [
  { id: "c1", name: "Acme Corp", slug: "acme-corp", icon: "acme.png", active_posting_count: 6, year_posting_count: 8 },
  { id: "c2", name: "Beta Labs", slug: "beta-labs", icon: "beta.png", active_posting_count: 5, year_posting_count: 7 },
  { id: "c3", name: "Gamma Tech", slug: "gamma-tech", icon: null, active_posting_count: 4, year_posting_count: 5 },
  { id: "c4", name: "Delta Systems", slug: "delta-systems", icon: "delta.png", active_posting_count: 3, year_posting_count: 4 },
  { id: "c5", name: "Epsilon AI", slug: "epsilon-ai", icon: null, active_posting_count: 2, year_posting_count: 3 },
];

// Location IDs used across postings
const LOC_BERLIN = 101;
const LOC_LONDON = 102;
const LOC_NYC = 103;
const LOC_REMOTE = 104;

// 22 job postings spread across companies
const RAW_JOB_POSTINGS = [
  // ── Acme Corp (c1) — 6 active, 2 inactive ──
  {
    id: "jp1", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "Senior Software Engineer", is_active: true,
    location_ids: [LOC_BERLIN], location_names: ["Berlin"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 1, seniority_id: 3, technology_ids: [10, 11], employment_type: "full-time",
    salary_eur: 85000, experience_min: 5, locales: ["en", "de"],
    first_seen_at: NOW_UNIX - 10 * 86400, source_url: "https://acme.com/jobs/1",
  },
  {
    id: "jp2", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "Junior Frontend Developer", is_active: true,
    location_ids: [LOC_LONDON], location_names: ["London"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 2, seniority_id: 1, technology_ids: [12, 13], employment_type: "full-time",
    salary_eur: 45000, experience_min: 1, locales: ["en"],
    first_seen_at: NOW_UNIX - 5 * 86400,
  },
  {
    id: "jp3", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "DevOps Engineer", is_active: true,
    location_ids: [LOC_REMOTE], location_names: ["Remote"], location_types: ["remote"], location_geo_types: ["macro"],
    occupation_id: 3, technology_ids: [14], employment_type: "full-time",
    salary_eur: 95000, experience_min: 3, locales: ["en"],
    first_seen_at: NOW_UNIX - 3 * 86400,
  },
  {
    id: "jp4", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "Data Scientist", is_active: true,
    location_ids: [LOC_BERLIN, LOC_REMOTE], location_names: ["Berlin", "Remote"], location_types: ["onsite", "remote"], location_geo_types: ["city", "macro"],
    occupation_id: 4, technology_ids: [15], employment_type: "full-time",
    experience_min: -1, locales: ["_none"],  // sentinel: no experience, no language detected
    first_seen_at: NOW_UNIX - 20 * 86400,
  },
  {
    id: "jp5", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "Product Manager", is_active: true,
    location_ids: [LOC_NYC], location_names: ["New York"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 5, technology_ids: [], employment_type: "full-time",
    salary_eur: 120000, experience_min: 7, locales: ["en"],
    first_seen_at: NOW_UNIX - 2 * 86400,
  },
  {
    id: "jp6", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "QA Engineer", is_active: true,
    location_ids: [LOC_LONDON], location_names: ["London"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 3, technology_ids: [10], employment_type: "contract",
    salary_eur: 55000, experience_min: 2, locales: ["en"],
    first_seen_at: NOW_UNIX - 1 * 86400,
  },
  {
    id: "jp7", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "Backend Developer", is_active: false,
    location_ids: [LOC_BERLIN], location_names: ["Berlin"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 2, technology_ids: [10, 14], employment_type: "full-time",
    salary_eur: 70000, experience_min: 3, locales: ["de"],
    first_seen_at: SIX_MONTHS_AGO,
  },
  {
    id: "jp8", company_id: "c1", company_name: "Acme Corp", company_slug: "acme-corp", company_icon: "acme.png",
    title: "Intern Software", is_active: false,
    location_ids: [LOC_BERLIN], location_names: ["Berlin"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 2, technology_ids: [], employment_type: "internship",
    experience_min: 0, locales: ["de"],
    first_seen_at: TWO_YEARS_AGO,
  },

  // ── Beta Labs (c2) — 5 active, 2 inactive ──
  {
    id: "jp9", company_id: "c2", company_name: "Beta Labs", company_slug: "beta-labs", company_icon: "beta.png",
    title: "Machine Learning Engineer", is_active: true,
    location_ids: [LOC_REMOTE], location_names: ["Remote"], location_types: ["remote"], location_geo_types: ["macro"],
    occupation_id: 4, seniority_id: 2, technology_ids: [15, 16], employment_type: "full-time",
    salary_eur: 110000, experience_min: 3, locales: ["en"],
    first_seen_at: NOW_UNIX - 7 * 86400,
  },
  {
    id: "jp10", company_id: "c2", company_name: "Beta Labs", company_slug: "beta-labs", company_icon: "beta.png",
    title: "Senior React Developer", is_active: true,
    location_ids: [LOC_LONDON], location_names: ["London"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 2, seniority_id: 3, technology_ids: [12, 13], employment_type: "full-time",
    salary_eur: 90000, experience_min: 5, locales: ["en"],
    first_seen_at: NOW_UNIX - 4 * 86400,
  },
  {
    id: "jp11", company_id: "c2", company_name: "Beta Labs", company_slug: "beta-labs", company_icon: "beta.png",
    title: "Platform Engineer", is_active: true,
    location_ids: [LOC_BERLIN, LOC_LONDON], location_names: ["Berlin", "London"], location_types: ["onsite", "onsite"], location_geo_types: ["city", "city"],
    occupation_id: 3, technology_ids: [14], employment_type: "full-time",
    salary_eur: 80000, experience_min: -1, locales: ["en", "de"],  // sentinel experience
    first_seen_at: NOW_UNIX - 15 * 86400,
  },
  {
    id: "jp12", company_id: "c2", company_name: "Beta Labs", company_slug: "beta-labs", company_icon: "beta.png",
    title: "Technical Writer", is_active: true,
    location_ids: [LOC_REMOTE], location_names: ["Remote"], location_types: ["remote"], location_geo_types: ["macro"],
    occupation_id: 6, technology_ids: [], employment_type: "part-time",
    experience_min: -1, locales: ["_none"],  // sentinel: no experience, no language
    first_seen_at: NOW_UNIX - 25 * 86400,
  },
  {
    id: "jp13", company_id: "c2", company_name: "Beta Labs", company_slug: "beta-labs", company_icon: "beta.png",
    title: "Security Analyst", is_active: true,
    location_ids: [LOC_NYC], location_names: ["New York"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 7, technology_ids: [17], employment_type: "full-time",
    salary_eur: 105000, experience_min: 4, locales: ["en"],
    first_seen_at: NOW_UNIX - 6 * 86400,
  },
  {
    id: "jp14", company_id: "c2", company_name: "Beta Labs", company_slug: "beta-labs", company_icon: "beta.png",
    title: "Mobile Developer", is_active: false,
    location_ids: [LOC_LONDON], location_names: ["London"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 2, technology_ids: [18], employment_type: "full-time",
    salary_eur: 65000, experience_min: 2, locales: ["en"],
    first_seen_at: SIX_MONTHS_AGO - 30 * 86400,
  },
  {
    id: "jp15", company_id: "c2", company_name: "Beta Labs", company_slug: "beta-labs", company_icon: "beta.png",
    title: "Cloud Architect", is_active: false,
    location_ids: [LOC_REMOTE], location_names: ["Remote"], location_types: ["remote"], location_geo_types: ["macro"],
    occupation_id: 3, technology_ids: [14], employment_type: "full-time",
    salary_eur: 130000, experience_min: 8, locales: ["en"],
    first_seen_at: TWO_YEARS_AGO + 30 * 86400,
  },

  // ── Gamma Tech (c3) — 4 active ──
  {
    id: "jp16", company_id: "c3", company_name: "Gamma Tech", company_slug: "gamma-tech",
    title: "Full Stack Developer", is_active: true,
    location_ids: [LOC_BERLIN], location_names: ["Berlin"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 2, seniority_id: 2, technology_ids: [10, 12, 13], employment_type: "full-time",
    salary_eur: 75000, experience_min: 3, locales: ["de"],
    first_seen_at: NOW_UNIX - 8 * 86400,
  },
  {
    id: "jp17", company_id: "c3", company_name: "Gamma Tech", company_slug: "gamma-tech",
    title: "Backend Engineer", is_active: true,
    location_ids: [LOC_BERLIN], location_names: ["Berlin"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 2, technology_ids: [10, 14], employment_type: "full-time",
    salary_eur: 70000, experience_min: 2, locales: ["de", "en"],
    first_seen_at: NOW_UNIX - 12 * 86400,
  },
  {
    id: "jp18", company_id: "c3", company_name: "Gamma Tech", company_slug: "gamma-tech",
    title: "UX Designer", is_active: true,
    location_ids: [LOC_BERLIN], location_names: ["Berlin"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 8, technology_ids: [], employment_type: "full-time",
    experience_min: -1, locales: ["de"],  // sentinel experience
    first_seen_at: NOW_UNIX - 14 * 86400,
  },
  {
    id: "jp19", company_id: "c3", company_name: "Gamma Tech", company_slug: "gamma-tech",
    title: "Software Tester", is_active: true,
    location_ids: [LOC_BERLIN], location_names: ["Berlin"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 3, technology_ids: [10], employment_type: "part-time",
    salary_eur: 50000, experience_min: 1, locales: ["de"],
    first_seen_at: NOW_UNIX - 18 * 86400,
  },

  // ── Delta Systems (c4) — 3 active ──
  {
    id: "jp20", company_id: "c4", company_name: "Delta Systems", company_slug: "delta-systems", company_icon: "delta.png",
    title: "Systems Engineer", is_active: true,
    location_ids: [LOC_NYC], location_names: ["New York"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 3, technology_ids: [14], employment_type: "full-time",
    salary_eur: 100000, experience_min: 4, locales: ["en"],
    first_seen_at: NOW_UNIX - 9 * 86400,
  },
  {
    id: "jp21", company_id: "c4", company_name: "Delta Systems", company_slug: "delta-systems", company_icon: "delta.png",
    title: "Junior Developer", is_active: true,
    location_ids: [LOC_NYC, LOC_REMOTE], location_names: ["New York", "Remote"], location_types: ["onsite", "remote"], location_geo_types: ["city", "macro"],
    occupation_id: 2, seniority_id: 1, technology_ids: [12], employment_type: "full-time",
    salary_eur: 55000, experience_min: 0, locales: ["en"],
    first_seen_at: NOW_UNIX - 11 * 86400,
  },
  {
    id: "jp22", company_id: "c4", company_name: "Delta Systems", company_slug: "delta-systems", company_icon: "delta.png",
    title: "Database Administrator", is_active: true,
    location_ids: [LOC_NYC], location_names: ["New York"], location_types: ["onsite"], location_geo_types: ["city"],
    occupation_id: 9, technology_ids: [19], employment_type: "full-time",
    salary_eur: 95000, experience_min: 5, locales: ["en"],
    first_seen_at: NOW_UNIX - 13 * 86400,
  },

  // ── Epsilon AI (c5) — 2 active ──
  {
    id: "jp23", company_id: "c5", company_name: "Epsilon AI", company_slug: "epsilon-ai",
    title: "AI Research Scientist", is_active: true,
    location_ids: [LOC_REMOTE], location_names: ["Remote"], location_types: ["remote"], location_geo_types: ["macro"],
    occupation_id: 4, seniority_id: 3, technology_ids: [15, 16], employment_type: "full-time",
    salary_eur: 150000, experience_min: 7, locales: ["en"],
    first_seen_at: NOW_UNIX - 6 * 86400,
  },
  {
    id: "jp24", company_id: "c5", company_name: "Epsilon AI", company_slug: "epsilon-ai",
    title: "NLP Engineer", is_active: true,
    location_ids: [LOC_LONDON, LOC_REMOTE], location_names: ["London", "Remote"], location_types: ["onsite", "remote"], location_geo_types: ["city", "macro"],
    occupation_id: 4, technology_ids: [15], employment_type: "full-time",
    salary_eur: 105000, experience_min: -1, locales: ["_none"],  // sentinel: no experience, no language
    first_seen_at: NOW_UNIX - 16 * 86400,
  },
];

const JOB_POSTINGS = RAW_JOB_POSTINGS.map((posting) => {
  const experienceMin = posting.experience_min;
  const experienceMax = experienceMin === -1 ? -1 : 99;
  return {
    ...posting,
    experience_max: experienceMax,
    experience_min_years: experienceMin,
    experience_max_years: experienceMax,
  };
});

// Postings that are active
const ACTIVE_POSTINGS = JOB_POSTINGS.filter((jp) => jp.is_active);
// Postings with sentinel experience (-1)
const _SENTINEL_EXPERIENCE_POSTINGS = JOB_POSTINGS.filter(
  (jp) => jp.experience_min === -1,
);
// Postings with sentinel locale (_none)
const _SENTINEL_LOCALE_POSTINGS = JOB_POSTINGS.filter((jp) =>
  jp.locales.includes("_none"),
);

// ── Collection schemas for test ─────────────────────────────────────

const JOB_POSTING_SCHEMA: CollectionCreateSchema = {
  name: JOB_POSTING_COLLECTION,
  fields: [
    { name: "company_id", type: "string", facet: true },
    { name: "company_name", type: "string", facet: true },
    { name: "company_slug", type: "string", index: false },
    { name: "company_icon", type: "string", index: false, optional: true },
    { name: "title", type: "string" },
    { name: "is_active", type: "bool", facet: true },
    // `has_content` mirrors the production schema (issue #2917). Seed
    // docs below do NOT set the field; the production filter
    // `has_content:!=false` then matches them by virtue of `!=false`
    // covering both `true` and absent values. This keeps the test data
    // shape representative of the post-deploy-pre-backfill state where
    // existing docs lack the field but stay visible until backfill.
    { name: "has_content", type: "bool", facet: true, optional: true },
    { name: "location_ids", type: "int32[]", facet: true },
    { name: "location_names", type: "string[]", facet: true },
    { name: "location_types", type: "string[]", facet: true },
    { name: "location_geo_types", type: "string[]", index: false },
    { name: "occupation_id", type: "int32", facet: true, optional: true },
    { name: "occupation_ids", type: "int32[]", facet: true, optional: true },
    { name: "seniority_id", type: "int32", facet: true, optional: true },
    { name: "technology_ids", type: "int32[]", facet: true },
    { name: "employment_type", type: "string", facet: true, optional: true },
    { name: "salary_eur", type: "int32", facet: true, optional: true },
    { name: "experience_min_years", type: "float", facet: true, optional: true },
    { name: "experience_max_years", type: "float", facet: true, optional: true },
    { name: "experience_min", type: "int32", facet: true },
    { name: "experience_max", type: "int32", facet: true, optional: true },
    { name: "locales", type: "string[]", facet: true },
    { name: "source_url", type: "string", index: false, optional: true },
    { name: "first_seen_at", type: "int64" },
    { name: "last_seen_at", type: "int64", optional: true },
  ],
  default_sorting_field: "first_seen_at",
  token_separators: ["-", "/"],
};

const COMPANY_SCHEMA: CollectionCreateSchema = {
  name: COMPANY_COLLECTION,
  fields: [
    { name: "id", type: "string" },
    { name: "name", type: "string" },
    { name: "slug", type: "string", index: false },
    { name: "icon", type: "string", index: false, optional: true },
    { name: "active_posting_count", type: "int32" },
    { name: "year_posting_count", type: "int32" },
  ],
  default_sorting_field: "active_posting_count",
};

// ── Default filter params (required by SearchFilters) ───────────────

const DEFAULT_FILTERS = {
  languages: [],
  locale: "en",
};

// ── Helpers ──────────────────────────────────────────────────────────

async function isTypesenseReachable(): Promise<boolean> {
  try {
    const client = new Client({
      nodes: [{ host: "localhost", port: 8108, protocol: "http" }],
      apiKey: API_KEY,
      connectionTimeoutSeconds: 2,
    });
    const health = await client.health.retrieve();
    return health.ok === true;
  } catch {
    return false;
  }
}

async function dropCollectionIfExists(name: string): Promise<void> {
  try {
    await adminClient.collections(name).delete();
  } catch {
    // ignore ObjectNotFound
  }
}

/**
 * Override the global singleton so the provider talks to our test collections.
 * We monkey-patch the collection names by aliasing them to the standard names.
 */
async function createAliases(): Promise<void> {
  // Create aliases: "job_posting" -> e2e_test_job_posting, etc.
  await adminClient.aliases().upsert("job_posting", {
    collection_name: JOB_POSTING_COLLECTION,
  });
  await adminClient.aliases().upsert("company", {
    collection_name: COMPANY_COLLECTION,
  });
}

async function removeAliases(): Promise<void> {
  for (const name of ["job_posting", "company"]) {
    try {
      await adminClient.aliases(name).delete();
    } catch {
      // ignore ObjectNotFound
    }
  }
}

// ── Suite setup ─────────────────────────────────────────────────────

beforeAll(async () => {
  const reachable = await isTypesenseReachable();
  if (!reachable) {
    if (shouldRequireTypesenseE2E()) {
      throw new Error(
        "Typesense not reachable at localhost:8108; refusing to skip Typesense E2E suite when CI/REQUIRE_TYPESENSE_E2E is set",
      );
    }
    suiteSkipped = true;
    console.warn(
      "Typesense not reachable at localhost:8108 — skipping E2E suite",
    );
    return;
  }

  adminClient = new Client({
    nodes: [{ host: "localhost", port: 8108, protocol: "http" }],
    apiKey: API_KEY,
    connectionTimeoutSeconds: 5,
  });

  // Clean up any leftover test data
  await removeAliases();
  await dropCollectionIfExists(JOB_POSTING_COLLECTION);
  await dropCollectionIfExists(COMPANY_COLLECTION);

  // Create collections
  await adminClient.collections().create(JOB_POSTING_SCHEMA);
  await adminClient.collections().create(COMPANY_SCHEMA);

  // Seed companies
  for (const company of COMPANIES) {
    await adminClient
      .collections(COMPANY_COLLECTION)
      .documents()
      .create(company);
  }

  // Seed job postings (add occupation_ids from occupation_id for ancestor denormalization)
  for (const posting of JOB_POSTINGS) {
    const doc = { ...posting, occupation_ids: posting.occupation_id != null ? [posting.occupation_id] : undefined };
    await adminClient
      .collections(JOB_POSTING_COLLECTION)
      .documents()
      .create(doc);
  }

  // Create aliases so the provider sees our test collections
  await createAliases();

  // Reset the global client singleton so a fresh one is created
  // pointing at the aliased collections.
  const g = globalThis as Record<string, unknown>;
  delete g.__typesenseSearchClient;

  provider = new TypesenseSearchProvider();

  // Small delay to let Typesense index
  await new Promise((resolve) => setTimeout(resolve, 500));
}, 30_000);

afterAll(async () => {
  if (suiteSkipped) return;

  await removeAliases();
  await dropCollectionIfExists(JOB_POSTING_COLLECTION);
  await dropCollectionIfExists(COMPANY_COLLECTION);
}, 15_000);

// ── Guard helper — skip individual tests when Typesense is down ─────

function skipIfUnavailable() {
  if (suiteSkipped) {
    return true;
  }
  return false;
}

// =====================================================================
// SearchProvider.search()
// =====================================================================

describe("search()", () => {
  it("single keyword returns companies with matching postings", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      offset: 0,
      limit: 10,
    });

    expect(result.companies.length).toBeGreaterThan(0);
    expect(result.totalCompanies).toBeGreaterThan(0);

    // Every returned posting should contain "Engineer" in its title (case-insensitive)
    for (const company of result.companies) {
      expect(company.postings.length).toBeGreaterThan(0);
      for (const posting of company.postings) {
        expect(posting.title?.toLowerCase()).toContain("engineer");
      }
    }
  });

  it("multiple keywords ranks by relevance", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Senior", "React", "Developer"],
      offset: 0,
      limit: 10,
    });

    expect(result.companies.length).toBeGreaterThan(0);

    // The first posting of the first company should be "Senior React Developer"
    // (matches all 3 keywords) — it's jp10 from Beta Labs
    const topPosting = result.companies[0].postings[0];
    expect(topPosting.title?.toLowerCase()).toContain("react");
    expect(topPosting.title?.toLowerCase()).toContain("developer");
  });

  it("typo tolerance returns results for misspelled keyword", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Develoer"], // missing 'p'
      offset: 0,
      limit: 10,
    });

    expect(result.companies.length).toBeGreaterThan(0);
    // Should still match developer positions
    const allTitles = result.companies.flatMap((c) =>
      c.postings.map((p) => p.title?.toLowerCase() ?? ""),
    );
    expect(allTitles.some((t) => t.includes("developer"))).toBe(true);
  });

  it("location filter restricts results", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      locationIds: [LOC_BERLIN],
      offset: 0,
      limit: 10,
    });

    expect(result.companies.length).toBeGreaterThan(0);
    // All returned posting IDs should be ones that include Berlin
    const berlinPostingIds = new Set(
      JOB_POSTINGS.filter(
        (jp) => jp.is_active && jp.location_ids.includes(LOC_BERLIN),
      ).map((jp) => jp.id),
    );
    for (const company of result.companies) {
      for (const posting of company.postings) {
        expect(berlinPostingIds.has(posting.id)).toBe(true);
      }
    }
  });

  it("salary range filter works", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      salaryMinEur: 80000,
      salaryMaxEur: 120000,
      offset: 0,
      limit: 10,
    });

    // Should return results — we have several engineer postings in this range
    expect(result.companies.length).toBeGreaterThan(0);

    // All returned postings should have salary in range (those that have a salary)
    const validPostingIds = new Set(
      JOB_POSTINGS.filter(
        (jp) =>
          jp.is_active &&
          jp.salary_eur !== undefined &&
          jp.salary_eur >= 80000 &&
          jp.salary_eur <= 120000,
      ).map((jp) => jp.id),
    );
    for (const company of result.companies) {
      for (const posting of company.postings) {
        expect(validPostingIds.has(posting.id)).toBe(true);
      }
    }
  });

  it("salaryMinEur=0 does not exclude salary-less postings", async () => {
    if (skipIfUnavailable()) return;

    // Search with salaryMinEur=0 — this should NOT activate the salary filter
    // because the guard checks > 0. So salary-less postings should be included.
    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Scientist"],
      salaryMinEur: 0,
      offset: 0,
      limit: 10,
    });

    // "Data Scientist" (jp4) has no salary_eur, "AI Research Scientist" (jp23) has 150000
    const returnedIds = new Set(
      result.companies.flatMap((c) => c.postings.map((p) => p.id)),
    );

    // jp4 (no salary) should be in results since salaryMinEur=0 doesn't filter
    expect(returnedIds.has("jp4")).toBe(true);
  });

  it("totalCompanies returns distinct company count", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Developer"],
      offset: 0,
      limit: 10,
    });

    // "Developer" appears in multiple companies
    expect(result.totalCompanies).toBeGreaterThan(0);
    // totalCompanies should be <= companies returned on this page
    // (or equal, since we have a small dataset and limit=10)
    expect(result.totalCompanies).toBeLessThanOrEqual(5);

    // totalCompanies should not equal total posting count
    const totalPostings = result.companies.reduce(
      (sum, c) => sum + c.activeMatches,
      0,
    );
    // totalCompanies < totalPostings when any company has > 1 match
    if (totalPostings > result.totalCompanies) {
      expect(result.totalCompanies).toBeLessThan(totalPostings);
    }
  });

  it("multiple filters combine with AND", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Developer"],
      locationIds: [LOC_LONDON],
      seniorityIds: [3], // Senior
      offset: 0,
      limit: 10,
    });

    // Should match "Senior React Developer" (jp10) in London with seniority_id=3
    if (result.companies.length > 0) {
      for (const company of result.companies) {
        for (const posting of company.postings) {
          const orig = JOB_POSTINGS.find((jp) => jp.id === posting.id);
          expect(orig?.location_ids).toContain(LOC_LONDON);
          expect(orig?.seniority_id).toBe(3);
        }
      }
    }
  });

  it("no results for nonsense keyword", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["xyznonexistentkeyword12345"],
      offset: 0,
      limit: 10,
    });

    expect(result.companies).toHaveLength(0);
    expect(result.totalCompanies).toBe(0);
  });

  it("pagination returns different company sets", async () => {
    if (skipIfUnavailable()) return;

    const page1 = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      offset: 0,
      limit: 2,
    });

    const page2 = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      offset: 2,
      limit: 2,
    });

    if (page1.companies.length > 0 && page2.companies.length > 0) {
      const page1Ids = new Set(page1.companies.map((c) => c.company.id));
      const page2Ids = new Set(page2.companies.map((c) => c.company.id));

      // No overlap between pages
      for (const id of page2Ids) {
        expect(page1Ids.has(id)).toBe(false);
      }
    }
  });
});

// =====================================================================
// SearchProvider.listTopCompanies()
// =====================================================================

describe("listTopCompanies()", () => {
  it("unfiltered returns companies sorted by freshest posting", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.listTopCompanies({
      ...DEFAULT_FILTERS,
      offset: 0,
      limit: 10,
    });

    expect(result.companies.length).toBeGreaterThan(0);
    expect(result.totalCompanies).toBeGreaterThan(0);

    // Companies should be sorted by each company's newest visible posting.
    for (let i = 1; i < result.companies.length; i++) {
      const previousNewest = new Date(
        result.companies[i - 1].postings[0].firstSeenAt,
      ).getTime();
      const currentNewest = new Date(
        result.companies[i].postings[0].firstSeenAt,
      ).getTime();
      expect(previousNewest).toBeGreaterThanOrEqual(
        currentNewest,
      );
    }
  });

  it("filtered restricts to matching companies", async () => {
    if (skipIfUnavailable()) return;

    // Filter by NYC location — only companies with NYC postings
    const result = await provider.listTopCompanies({
      ...DEFAULT_FILTERS,
      locationIds: [LOC_NYC],
      offset: 0,
      limit: 10,
    });

    expect(result.companies.length).toBeGreaterThan(0);
    // All companies should have NYC postings
    const nycCompanyIds = new Set(
      JOB_POSTINGS.filter(
        (jp) => jp.is_active && jp.location_ids.includes(LOC_NYC),
      ).map((jp) => jp.company_id),
    );
    for (const company of result.companies) {
      expect(nycCompanyIds.has(company.company.id)).toBe(true);
    }
  });

  it("filtered totalCompanies is less than or equal to unfiltered", async () => {
    if (skipIfUnavailable()) return;

    const unfiltered = await provider.listTopCompanies({
      ...DEFAULT_FILTERS,
      offset: 0,
      limit: 10,
    });

    const filtered = await provider.listTopCompanies({
      ...DEFAULT_FILTERS,
      locationIds: [LOC_NYC],
      offset: 0,
      limit: 10,
    });

    expect(filtered.totalCompanies).toBeLessThanOrEqual(
      unfiltered.totalCompanies,
    );
  });
});

// =====================================================================
// SearchProvider.loadPostings()
// =====================================================================

describe("loadPostings()", () => {
  it("returns postings for a specific company", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.loadPostings({
      ...DEFAULT_FILTERS,
      companyId: "c1",
      keywords: [],
      offset: 0,
      limit: 20,
    });

    expect(result.length).toBeGreaterThan(0);
    // All returned postings should belong to Acme Corp (c1)
    const acmeActiveIds = new Set(
      JOB_POSTINGS.filter((jp) => jp.company_id === "c1" && jp.is_active).map(
        (jp) => jp.id,
      ),
    );
    for (const posting of result) {
      expect(acmeActiveIds.has(posting.id)).toBe(true);
    }
  });

  it("with keywords sorts by relevance", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.loadPostings({
      ...DEFAULT_FILTERS,
      companyId: "c1",
      keywords: ["Software"],
      offset: 0,
      limit: 20,
    });

    expect(result.length).toBeGreaterThan(0);
    // First result should match "Software" most closely
    expect(result[0].title?.toLowerCase()).toContain("software");
  });

  it("without keywords sorts by recency", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.loadPostings({
      ...DEFAULT_FILTERS,
      companyId: "c1",
      keywords: [],
      offset: 0,
      limit: 20,
    });

    expect(result.length).toBeGreaterThan(1);
    // Should be sorted by firstSeenAt descending
    for (let i = 1; i < result.length; i++) {
      const prev = new Date(result[i - 1].firstSeenAt).getTime();
      const curr = new Date(result[i].firstSeenAt).getTime();
      expect(prev).toBeGreaterThanOrEqual(curr);
    }
  });
});

// =====================================================================
// SearchProvider.loadPostingsWithCounts()
// =====================================================================

describe("loadPostingsWithCounts()", () => {
  it("returns activeCount and yearCount", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.loadPostingsWithCounts({
      ...DEFAULT_FILTERS,
      companyId: "c1",
      keywords: [],
      offset: 0,
      limit: 20,
    });

    expect(result.postings.length).toBeGreaterThan(0);
    expect(result.activeCount).toBeGreaterThan(0);
    // yearCount >= activeCount because all active postings are within the year
    // (our seed data has active postings within 25 days of NOW)
    expect(result.yearCount).toBeGreaterThanOrEqual(result.activeCount);
  });

  it("activeCount matches expected active postings for company", async () => {
    if (skipIfUnavailable()) return;

    const result = await provider.loadPostingsWithCounts({
      ...DEFAULT_FILTERS,
      companyId: "c2",
      keywords: [],
      offset: 0,
      limit: 20,
    });

    const expectedActive = JOB_POSTINGS.filter(
      (jp) => jp.company_id === "c2" && jp.is_active,
    ).length;
    expect(result.activeCount).toBe(expectedActive);
  });
});

// =====================================================================
// Salary histogram
// =====================================================================

describe("getSalaryHistogram()", () => {
  it("returns 10K EUR buckets with counts", async () => {
    if (skipIfUnavailable()) return;

    const buckets = await provider.getSalaryHistogram();

    expect(buckets.length).toBeGreaterThan(0);
    // Each bucket should have width of 10000 (standard range) or be the overflow bucket
    for (const bucket of buckets) {
      expect(bucket.count).toBeGreaterThan(0);
      const width = bucket.max - bucket.min;
      // Standard buckets have width 10000; overflow bucket (300k+) is much larger
      expect(width === 10000 || bucket.min >= 300000).toBe(true);
    }
  });

  it("total salary count matches expected active postings with salary", async () => {
    if (skipIfUnavailable()) return;

    const buckets = await provider.getSalaryHistogram();
    const totalCount = buckets.reduce((sum, b) => sum + b.count, 0);

    const expectedWithSalary = ACTIVE_POSTINGS.filter(
      (jp) => jp.salary_eur !== undefined && jp.salary_eur > 0,
    ).length;
    expect(totalCount).toBe(expectedWithSalary);
  });

  it("bucket boundaries are sorted ascending", async () => {
    if (skipIfUnavailable()) return;

    const buckets = await provider.getSalaryHistogram();
    for (let i = 1; i < buckets.length; i++) {
      expect(buckets[i].min).toBeGreaterThanOrEqual(buckets[i - 1].min);
    }
  });

  it("respects filters — filtered total <= unfiltered total", async () => {
    if (skipIfUnavailable()) return;

    const unfiltered = await provider.getSalaryHistogram();
    const filtered = await provider.getSalaryHistogram({
      locationIds: [LOC_BERLIN],
    });

    const unfilteredTotal = unfiltered.reduce((s, b) => s + b.count, 0);
    const filteredTotal = filtered.reduce((s, b) => s + b.count, 0);
    expect(filteredTotal).toBeLessThanOrEqual(unfilteredTotal);
  });
});

// =====================================================================
// Experience histogram
// =====================================================================

describe("getExperienceHistogram()", () => {
  it("returns year buckets with non-negative years", async () => {
    if (skipIfUnavailable()) return;

    const buckets = await provider.getExperienceHistogram();

    expect(buckets.length).toBeGreaterThan(0);
    for (const bucket of buckets) {
      expect(bucket.years).toBeGreaterThanOrEqual(0);
      expect(bucket.count).toBeGreaterThan(0);
    }
  });

  it("excludes sentinel -1 values", async () => {
    if (skipIfUnavailable()) return;

    const buckets = await provider.getExperienceHistogram();

    // No bucket should have years === -1
    const sentinelBucket = buckets.find((b) => b.years === -1);
    expect(sentinelBucket).toBeUndefined();

    // Total count should exclude the sentinel postings
    const totalCount = buckets.reduce((sum, b) => sum + b.count, 0);
    const expectedWithExperience = ACTIVE_POSTINGS.filter(
      (jp) => jp.experience_min >= 0,
    ).length;
    expect(totalCount).toBe(expectedWithExperience);
  });

  it("buckets are sorted by years ascending", async () => {
    if (skipIfUnavailable()) return;

    const buckets = await provider.getExperienceHistogram();
    for (let i = 1; i < buckets.length; i++) {
      expect(buckets[i].years).toBeGreaterThanOrEqual(buckets[i - 1].years);
    }
  });
});

// =====================================================================
// Sentinel value tests
// =====================================================================

describe("sentinel values", () => {
  it("experience filter includes jobs with sentinel -1 (no experience requirement)", async () => {
    if (skipIfUnavailable()) return;

    // Search with experienceMax=5 — should include sentinel -1 postings
    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      experienceMax: 5,
      offset: 0,
      limit: 20,
    });

    const returnedIds = new Set(
      result.companies.flatMap((c) => c.postings.map((p) => p.id)),
    );

    // jp11 (Platform Engineer, experience_min=-1) should be included
    // because the filter is (experience_min <= 5 || experience_min == -1)
    expect(returnedIds.has("jp11")).toBe(true);
  });

  it("language filter includes jobs with sentinel _none locale", async () => {
    if (skipIfUnavailable()) return;

    // Search with languages=["en"] — the filter builder appends _none automatically
    const result = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Scientist"],
      languages: ["en"],
      offset: 0,
      limit: 20,
    });

    const returnedIds = new Set(
      result.companies.flatMap((c) => c.postings.map((p) => p.id)),
    );

    // jp4 "Data Scientist" has locales=["_none"] — should be included
    expect(returnedIds.has("jp4")).toBe(true);

    // jp23 "AI Research Scientist" has locales=["en"] — should also be included
    expect(returnedIds.has("jp23")).toBe(true);
  });

  it("salary filter with min=0 does not exclude salary-less postings", async () => {
    if (skipIfUnavailable()) return;

    // With salaryMinEur=0, the salary filter should NOT activate
    // So postings without salary_eur should still appear
    const withZero = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      salaryMinEur: 0,
      offset: 0,
      limit: 20,
    });

    const withoutSalary = await provider.search({
      ...DEFAULT_FILTERS,
      keywords: ["Engineer"],
      offset: 0,
      limit: 20,
    });

    // Both should return the same results since salaryMinEur=0 is a no-op
    const idsWithZero = new Set(
      withZero.companies.flatMap((c) => c.postings.map((p) => p.id)),
    );
    const idsWithout = new Set(
      withoutSalary.companies.flatMap((c) => c.postings.map((p) => p.id)),
    );

    expect(idsWithZero).toEqual(idsWithout);
  });
});

// =====================================================================
// Graceful degradation
// =====================================================================

describe("graceful degradation", () => {
  it("search returns degraded result when Typesense is unreachable", async () => {
    if (skipIfUnavailable()) return;

    // Temporarily point global client at a wrong host
    const g = globalThis as Record<string, unknown>;
    const savedClient = g.__typesenseSearchClient;

    const badClient = new Client({
      nodes: [{ host: "localhost", port: 19999, protocol: "http" }],
      apiKey: "wrong_key",
      connectionTimeoutSeconds: 1,
    });
    g.__typesenseSearchClient = badClient;

    try {
      const badProvider = new TypesenseSearchProvider();
      const result = await badProvider.search({
        ...DEFAULT_FILTERS,
        keywords: ["Engineer"],
        offset: 0,
        limit: 10,
      });

      expect(result.companies).toHaveLength(0);
      expect(result.totalCompanies).toBe(0);
      expect(result.degraded).toBe(true);
    } finally {
      // Restore the original client
      g.__typesenseSearchClient = savedClient;
    }
  }, 15_000);

  it("loadPostings returns empty array when unreachable", async () => {
    if (skipIfUnavailable()) return;

    const g = globalThis as Record<string, unknown>;
    const savedClient = g.__typesenseSearchClient;

    const badClient = new Client({
      nodes: [{ host: "localhost", port: 19999, protocol: "http" }],
      apiKey: "wrong_key",
      connectionTimeoutSeconds: 1,
    });
    g.__typesenseSearchClient = badClient;

    try {
      const badProvider = new TypesenseSearchProvider();
      const result = await badProvider.loadPostings({
        ...DEFAULT_FILTERS,
        companyId: "c1",
        keywords: [],
        offset: 0,
        limit: 10,
      });

      expect(result).toHaveLength(0);
    } finally {
      g.__typesenseSearchClient = savedClient;
    }
  }, 15_000);

  it("getSalaryHistogram returns empty array when unreachable", async () => {
    if (skipIfUnavailable()) return;

    const g = globalThis as Record<string, unknown>;
    const savedClient = g.__typesenseSearchClient;

    const badClient = new Client({
      nodes: [{ host: "localhost", port: 19999, protocol: "http" }],
      apiKey: "wrong_key",
      connectionTimeoutSeconds: 1,
    });
    g.__typesenseSearchClient = badClient;

    try {
      const badProvider = new TypesenseSearchProvider();
      const result = await badProvider.getSalaryHistogram();

      expect(result).toHaveLength(0);
    } finally {
      g.__typesenseSearchClient = savedClient;
    }
  }, 15_000);

  it("loadPostingsWithCounts returns zeroed result when unreachable", async () => {
    if (skipIfUnavailable()) return;

    const g = globalThis as Record<string, unknown>;
    const savedClient = g.__typesenseSearchClient;

    const badClient = new Client({
      nodes: [{ host: "localhost", port: 19999, protocol: "http" }],
      apiKey: "wrong_key",
      connectionTimeoutSeconds: 1,
    });
    g.__typesenseSearchClient = badClient;

    try {
      const badProvider = new TypesenseSearchProvider();
      const result = await badProvider.loadPostingsWithCounts({
        ...DEFAULT_FILTERS,
        companyId: "c1",
        keywords: [],
        offset: 0,
        limit: 10,
      });

      expect(result.postings).toHaveLength(0);
      expect(result.activeCount).toBe(0);
      expect(result.yearCount).toBe(0);
    } finally {
      g.__typesenseSearchClient = savedClient;
    }
  }, 15_000);
});
