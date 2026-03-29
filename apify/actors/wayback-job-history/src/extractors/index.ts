import { load } from 'cheerio';
import { log } from 'apify';
import type { ExtractionResult } from '../types.js';
import type { CdxSnapshot } from '../cdx.js';
import { extractFromJsonLd } from './jsonld.js';
import { extractFromNextData, findJobsInObject } from './nextdata.js';
import { extractGreenhouseToken, extractFromGreenhouse } from './greenhouse.js';
import { extractLeverSlug, extractFromLever } from './lever.js';
import { extractAshbySlug, extractFromAshby } from './ashby.js';
import { extractWorkableSlug, extractFromWorkable } from './workable.js';
import { extractWorkdayParams, extractFromWorkday } from './workday.js';
import { extractSRCompany, extractFromSmartRecruiters } from './smartrecruiters.js';
import { extractGeneric } from './generic.js';

/**
 * Main extraction dispatcher.
 *
 * Priority order:
 * 1. Known ATS API (Greenhouse / Lever / Ashby / Workable / SmartRecruiters) — most reliable, structured data
 * 2. JSON-LD JobPosting schema
 * 3. Next.js __NEXT_DATA__ recursive walk
 * 4. window.__data / other globals embedded in <script> tags
 * 5. Generic CSS selector heuristics
 */
export async function extractJobs(
  html: string,
  snapshot: CdxSnapshot,
): Promise<ExtractionResult> {
  let url: URL;
  try {
    url = new URL(snapshot.original);
  } catch {
    log.warning(`Cannot parse URL: ${snapshot.original}`);
    return { jobs: [], method: 'error' };
  }

  // ── 1. ATS API via Wayback ──────────────────────────────────────────────────
  if (extractGreenhouseToken(url)) {
    const result = await extractFromGreenhouse(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractLeverSlug(url)) {
    const result = await extractFromLever(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractAshbySlug(url)) {
    const result = await extractFromAshby(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractWorkableSlug(url)) {
    const result = await extractFromWorkable(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractWorkdayParams(url)) {
    const result = await extractFromWorkday(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractSRCompany(url)) {
    const result = await extractFromSmartRecruiters(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  // ── 2–5. HTML-based extractors ─────────────────────────────────────────────
  const $ = load(html);

  // JSON-LD
  const jsonldResult = extractFromJsonLd($);
  if (jsonldResult.jobs.length > 0) return jsonldResult;

  // __NEXT_DATA__
  const nextResult = extractFromNextData($);
  if (nextResult.jobs.length > 0) return nextResult;

  // Other globals embedded in <script> tags (window.__data, INITIAL_STATE, etc.)
  const globalsResult = extractFromScriptGlobals($);
  if (globalsResult.jobs.length > 0) return globalsResult;

  // Generic CSS
  const genericResult = extractGeneric($, url);
  if (genericResult.jobs.length > 0) return genericResult;

  return { jobs: [], method: 'none' };
}

/**
 * Try to extract job data from common window globals serialized in <script> blocks.
 */
function extractFromScriptGlobals($: ReturnType<typeof load>): ExtractionResult {
  const patterns = [
    // window.__data = {...}
    /window\.__data\s*=\s*(\{[\s\S]*?\});?\s*(?:window\.|$)/,
    // window.__INITIAL_STATE__ = {...}
    /window\.__INITIAL_STATE__\s*=\s*(\{[\s\S]*?\});/,
    // var pageData = {...}
    /(?:var|let|const)\s+pageData\s*=\s*(\{[\s\S]*?\});/,
    // window.jobListings = [...]
    /window\.(?:jobListings|jobs|jobData)\s*=\s*(\[[\s\S]*?\]);/,
  ];

  const scripts = $('script:not([src])').map((_, el) => $(el).html() ?? '').get();

  for (const script of scripts) {
    for (const pattern of patterns) {
      const match = pattern.exec(script);
      if (!match) continue;
      try {
        const data: unknown = JSON.parse(match[1]);
        const result = findJobsInObject(data, 'script-globals');
        if (result.jobs.length > 0) return result;
      } catch {
        // malformed JS literal — skip
      }
    }
  }

  return { jobs: [], method: 'script-globals' };
}
