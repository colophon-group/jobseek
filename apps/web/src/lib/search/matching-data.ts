import "server-only";
import { AsyncLocalStorage } from "node:async_hooks";

const reads = new AsyncLocalStorage<Map<string, Promise<unknown>>>();
/** Share taxonomy reads inside one bounded host slice; never cache across slices. */
export function withMatchingDataReads<T>(operation: () => Promise<T>): Promise<T> {
  return reads.run(new Map(), operation);
}
function memo<T>(key: unknown[], operation: () => Promise<T>): Promise<T> {
  const cache = reads.getStore();
  if (!cache || cache.size >= 128) return operation();
  const encoded = JSON.stringify(key);
  let pending = cache.get(encoded) as Promise<T> | undefined;
  if (!pending) { pending = operation(); cache.set(encoded, pending); }
  return pending;
}

import { fetchIndexedPostingDetail } from "./typesense-posting-detail";
import { fetchLocationDocumentsBySlugs, fetchLocationDocumentsByIds,
  fetchOccupationDocuments, fetchSeniorityDocuments, fetchTechnologyDocuments } from "./typesense-taxonomy";
import { db } from "@/db";
import { sql } from "drizzle-orm";
import { withDbRetry } from "@/lib/db-retry";
import type { LocationType } from "./types";

/** Framework-free reads for host workers; the web adapter retains Next.js caches. */

export interface PostingDetail {
  id: string;
  title: string | null;
  company: { id: string; name: string; slug: string; logo: string | null; icon: string | null };
  locations: { id: number; name: string; type: string; geoType?: string; parentName?: string }[];
  employmentType: string | null;
  experienceMin: number | null;
  experienceMax: number | null;
  technologies: { id: number; name: string }[];
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string | null;
  salaryPeriod: string | null;
  seniority: { id: number; slug: string; name: string } | null;
  sourceUrl: string;
  firstSeenAt: string;
  descriptionHtml: string | null;
  descriptionUrl: string | null;
}


const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function getPostingDetail(params: {
  postingId: string;
  locale: string;
}): Promise<PostingDetail | null> {
  const { postingId, locale } = params;
  if (!UUID_RE.test(postingId)) return null;
  return fetchPostingDetail(postingId, locale);
}


async function fetchPostingDetail(
  postingId: string,
  locale: string,
): Promise<PostingDetail | null> {
  const posting = await fetchIndexedPostingDetail(postingId, locale);
  if (!posting) return null;

  // Build R2 description URL for client-side fetch
  const r2Domain = process.env.R2_DOMAIN_URL;
  let descriptionUrl: string | null = null;
  if (r2Domain) {
    descriptionUrl = `${r2Domain.replace(/\/$/, "")}/job/${postingId}/${posting.descriptionLocale}/latest.html`;
  }

  return {
    id: posting.id,
    title: posting.title,
    company: posting.company,
    locations: posting.locations,
    employmentType: posting.employmentType,
    experienceMin: posting.experienceMin,
    experienceMax: posting.experienceMax,
    technologies: posting.technologies,
    salaryMin: posting.salaryMin,
    salaryMax: posting.salaryMax,
    salaryCurrency: posting.salaryCurrency,
    salaryPeriod: posting.salaryPeriod,
    seniority: posting.seniority?.slug && posting.seniority.name
      ? posting.seniority
      : null,
    sourceUrl: posting.sourceUrl,
    firstSeenAt: posting.firstSeenAt,
    descriptionHtml: null,
    descriptionUrl,
  };
}


export interface CurrencyRate {
  currency: string;
  toEur: number;
}

// Cold-build / transient-database fallback for the server-embedded table.
// Keep this aligned with drizzle/0063_add_salary_experience.sql. Returning the
// full seeded set matters now that app shells serialize these rates: an
// EUR-only fallback would remove every other currency from the selector and
// make non-EUR salary filters use an identity conversion until that route's
// shell revalidated.
const FALLBACK_CURRENCY_RATES: CurrencyRate[] = [
  { currency: "EUR", toEur: 1 },
  { currency: "USD", toEur: 0.87 },
  { currency: "GBP", toEur: 1.17 },
  { currency: "CHF", toEur: 1.04 },
  { currency: "CAD", toEur: 0.67 },
  { currency: "SEK", toEur: 0.089 },
  { currency: "NOK", toEur: 0.086 },
  { currency: "DKK", toEur: 0.134 },
  { currency: "PLN", toEur: 0.233 },
  { currency: "CZK", toEur: 0.04 },
  { currency: "HUF", toEur: 0.0025 },
  { currency: "RON", toEur: 0.201 },
  { currency: "AUD", toEur: 0.58 },
  { currency: "NZD", toEur: 0.53 },
  { currency: "JPY", toEur: 0.0061 },
  { currency: "ISK", toEur: 0.0068 },
  { currency: "TRY", toEur: 0.026 },
  { currency: "BGN", toEur: 0.511 },
  { currency: "BRL", toEur: 0.16 },
  { currency: "CNY", toEur: 0.126 },
  { currency: "HKD", toEur: 0.112 },
  { currency: "IDR", toEur: 0.000055 },
  { currency: "INR", toEur: 0.0103 },
  { currency: "KRW", toEur: 0.00065 },
  { currency: "MXN", toEur: 0.048 },
  { currency: "MYR", toEur: 0.2 },
  { currency: "PHP", toEur: 0.016 },
  { currency: "SGD", toEur: 0.68 },
  { currency: "THB", toEur: 0.025 },
  { currency: "ZAR", toEur: 0.05 },
  { currency: "ILS", toEur: 0.25 },
];

function fallbackCurrencyRates(): CurrencyRate[] {
  return FALLBACK_CURRENCY_RATES.map((rate) => ({ ...rate }));
}


async function getCurrencyRatesUncached(): Promise<CurrencyRate[]> {
  try {
    const rows = await withDbRetry(() => db.execute<{currency:string;to_eur:string}>(
      sql`SELECT currency,to_eur FROM currency_rate ORDER BY currency`), {label:"currencyRates"});
    return rows.map(row => ({currency:row.currency,toEur:Number(row.to_eur)}));
  } catch { return fallbackCurrencyRates(); }
}

export interface ResolvedLocation {
  id: number;
  slug: string;
  name: string;
  type: LocationType;
  parentName: string | null;
}


async function resolveLocationSlugsUncached(sortedSlugs: string[],locale: string): Promise<Map<string, ResolvedLocation>> {
  if (!sortedSlugs.length) return new Map();
  const abortSignal = undefined;

  const rows = abortSignal
    ? await fetchLocationDocumentsBySlugs(sortedSlugs, abortSignal)
    : await fetchLocationDocumentsBySlugs(sortedSlugs);
  const parentIds = rows.flatMap((row) => row.parent_id == null ? [] : [row.parent_id]);
  const parents = abortSignal
    ? await fetchLocationDocumentsByIds(parentIds, abortSignal)
    : await fetchLocationDocumentsByIds(parentIds);
  const parentsById = new Map(parents.map((parent) => [parent.location_id, parent]));
  const result: Record<string, ResolvedLocation> = {};
  for (const row of rows) {
    const parent = row.parent_id == null ? undefined : parentsById.get(row.parent_id);
    result[row.slug] = {
      id: row.location_id,
      slug: row.slug,
      name: locationName(row, locale),
      type: row.type as LocationType,
      parentName: parent ? locationName(parent, locale) : row.parent_name ?? null,
    };
  }
  return new Map(Object.entries(result));
}


function locationName(row: {slug:string;name_en:string;name_de?:string;name_fr?:string;name_it?:string},locale:string) {
  return ({en:row.name_en,de:row.name_de,fr:row.name_fr,it:row.name_it}[locale]) ?? row.name_en ?? row.slug;
}
export interface TaxonomySuggestion { id:number;slug:string;name:string;matchedName?:string; }

async function resolveOccupationSlugsUncached(slugs:string[],locale:string):Promise<Map<string,TaxonomySuggestion>> {
  if (!slugs.length) return new Map();
  const wanted=new Set(slugs);
  const rows=await fetchOccupationDocuments(locale);
  return new Map(rows.filter(row=>wanted.has(row.slug)).map(row=>[row.slug,{id:row.occupation_id,slug:row.slug,name:row.name}]));
}

async function resolveSenioritySlugsUncached(slugs:string[],locale:string):Promise<Map<string,TaxonomySuggestion>> {
  if (!slugs.length) return new Map();
  const wanted=new Set(slugs);
  const rows=await fetchSeniorityDocuments(locale);
  return new Map(rows.filter(row=>wanted.has(row.slug)).map(row=>[row.slug,{id:row.seniority_id,slug:row.slug,name:row.name}]));
}

async function resolveTechnologySlugsUncached(slugs:string[]):Promise<Map<string,TaxonomySuggestion>> {
  if (!slugs.length) return new Map();
  const wanted=new Set(slugs);
  const rows=await fetchTechnologyDocuments();
  return new Map(rows.filter(row=>wanted.has(row.slug)).map(row=>[row.slug,{id:row.technology_id,slug:row.slug,name:row.name ?? row.slug}]));
}

export function getCurrencyRates() {
  return memo(["currency"], getCurrencyRatesUncached);
}
export function resolveLocationSlugs(slugs:string[],locale:string) {
  return memo(["locations",locale,[...slugs].sort()],()=>resolveLocationSlugsUncached(slugs,locale));
}
export function resolveOccupationSlugs(slugs:string[],locale:string) {
  return memo(["occupations",locale,[...slugs].sort()],()=>resolveOccupationSlugsUncached(slugs,locale));
}
export function resolveSenioritySlugs(slugs:string[],locale:string) {
  return memo(["seniorities",locale,[...slugs].sort()],()=>resolveSenioritySlugsUncached(slugs,locale));
}
export function resolveTechnologySlugs(slugs:string[]) {
  return memo(["technologies",[...slugs].sort()],()=>resolveTechnologySlugsUncached(slugs));
}
