import { load } from 'cheerio';
import { log } from 'apify';
import type { ExtractionResult } from '../types.js';
import type { CdxSnapshot } from '../types.js';
import { extractFromJsonLd } from './jsonld.js';
import { extractFromNextData, findJobsInObject } from './nextdata.js';
import { extractGreenhouseToken, extractFromGreenhouse } from './greenhouse.js';
import { extractLeverSlug, extractFromLever } from './lever.js';
import { extractAshbySlug, extractFromAshby } from './ashby.js';
import { extractWorkableSlug, extractFromWorkable } from './workable.js';
import { extractWorkdayParams, extractFromWorkday } from './workday.js';
import { extractSRCompany, extractFromSmartRecruiters } from './smartrecruiters.js';
import { extractBambooHRSlug, extractFromBambooHR, extractICIMSSlug, extractFromICIMS } from './bamboohr.js';
import { extractRecruiteeSlug, extractFromRecruitee } from './recruitee.js';
import { extractJazzHRSlug, extractFromJazzHR, extractTaleoSlug, extractFromTaleo, extractJobviteSlug, extractFromJobvite } from './jazzhr.js';
import { extractTeamtailorSlug, extractFromTeamtailor } from './teamtailor.js';
import { extractPersonioSlug, extractFromPersonio } from './personio.js';
import { extractBreezySlug, extractFromBreezyHR } from './breezyhr.js';
import { extractSoftgardenSlug, extractFromSoftgarden } from './softgarden.js';
import { extractPinpointSlug, extractFromPinpoint } from './pinpoint.js';
import { extractComeetSlug, extractFromComeet } from './comeet.js';
import { extractFountainSlug, extractFromFountain } from './fountain.js';
import { extractRipplingSlug, extractFromRippling } from './rippling.js';
import { extractGeneric } from './generic.js';

/**
 * Main extraction dispatcher.
 *
 * Priority order:
 * 1. Known ATS API (Greenhouse / Lever / Ashby / Workable / SmartRecruiters / BambooHR / iCIMS / Recruitee / JazzHR / Teamtailor / BreezyHR / Softgarden / Pinpoint / Comeet / Fountain) — most reliable, structured data
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

  if (extractBambooHRSlug(url)) {
    const result = await extractFromBambooHR(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractICIMSSlug(url)) {
    const result = await extractFromICIMS(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractRecruiteeSlug(url)) {
    const result = await extractFromRecruitee(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractJazzHRSlug(url)) {
    const result = await extractFromJazzHR(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractTaleoSlug(url)) {
    const result = await extractFromTaleo(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractJobviteSlug(url)) {
    const result = await extractFromJobvite(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractTeamtailorSlug(url)) {
    const result = await extractFromTeamtailor(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractPersonioSlug(url)) {
    const result = await extractFromPersonio(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractBreezySlug(url)) {
    const result = await extractFromBreezyHR(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractSoftgardenSlug(url)) {
    const result = await extractFromSoftgarden(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractPinpointSlug(url)) {
    const result = await extractFromPinpoint(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractComeetSlug(url)) {
    const result = await extractFromComeet(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractFountainSlug(url)) {
    const result = await extractFromFountain(url, snapshot.timestamp);
    if (result.jobs.length > 0) return result;
  }

  if (extractRipplingSlug(url)) {
    const result = await extractFromRippling(url, snapshot.timestamp);
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
    // SuccessFactors: window.sfConfig = {...}
    /window\.sfConfig\s*=\s*(\{[\s\S]*?\});/,
    // SuccessFactors / generic: window.APP_DATA = {...}
    /window\.(?:APP_DATA|appData|APP_STATE|appState|pageConfig)\s*=\s*(\{[\s\S]*?\});/,
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
