import { log } from 'apify';
import { load } from 'cheerio';
import { fetchCdxSnapshots } from './cdx.js';
import { fetchArchivedPage, fetchArchivedJson } from './fetch.js';
import { normalizeTitle } from './match.js';
import type { CdxSnapshot, JobSighting } from './types.js';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

/**
 * Collect all unique jobs seen on a company career portal over a date range,
 * using Wayback Machine CDX snapshots.
 *
 * For known ATS platforms (Greenhouse, Lever, Ashby, SmartRecruiters, Workable)
 * the actor CDXes their API endpoints directly — those are well-archived and include
 * structured datePosted data. For unknown portals it falls back to HTML snapshots.
 *
 * Returns a map of normalizedTitle → JobSighting (first sighting only).
 */
export async function collectCareerJobs(
  portalUrl: string,
  startDate: string,
  endDate: string,
  maxSnapshots: number,
  delayMs: number,
): Promise<Map<string, JobSighting>> {
  const jobs = new Map<string, JobSighting>();

  // Detect ATS type and pick the best CDX target URL
  const atsInfo = detectAts(portalUrl);
  if (atsInfo) {
    log.info(`Career portal: detected ${atsInfo.type}, using API CDX URL`, { apiUrl: atsInfo.apiUrl });
    await collectFromAtsApi(jobs, atsInfo, startDate, endDate, maxSnapshots, delayMs);
  }

  // Also try the portal HTML page for extra coverage (or as fallback)
  if (jobs.size === 0) {
    log.info('Career portal: falling back to HTML snapshot extraction', { url: portalUrl });
    const snapshots = await fetchCdxSnapshots({ url: portalUrl, startDate, endDate, maxSnapshots });
    log.info(`Career portal: ${snapshots.length} HTML snapshots found`, { url: portalUrl });
    for (let i = 0; i < snapshots.length; i++) {
      const snap = snapshots[i];
      const date = snapshotToDate(snap.timestamp);
      log.info(`[career-html ${i + 1}/${snapshots.length}] ${date}`);
      const html = await fetchArchivedPage(snap.timestamp, snap.original);
      if (html) mergeJobs(jobs, extractFromHtml(html, snap), snap);
      if (i < snapshots.length - 1) await sleep(delayMs);
    }
  }

  log.info(`Career portal: ${jobs.size} unique jobs collected`);
  return jobs;
}

interface AtsInfo {
  type: string;
  apiUrl: string;
  /** Extract jobs from an archived API response body. */
  extract: (body: unknown) => Partial<JobSighting>[];
}

/** Detect the ATS platform from a portal URL and return CDX target + extractor. */
function detectAts(portalUrl: string): AtsInfo | null {
  let url: URL;
  try { url = new URL(portalUrl); } catch { return null; }

  // Greenhouse: boards.greenhouse.io/{token}
  if (/^boards\.greenhouse\.io$/i.test(url.hostname)) {
    const token = url.pathname.split('/').filter(Boolean)[0];
    if (!token) return null;
    return {
      type: 'greenhouse',
      apiUrl: `https://boards-api.greenhouse.io/v1/boards/${token}/jobs?content=false`,
      extract: (body) => {
        const data = body as { jobs?: GhJob[] };
        return (data.jobs ?? []).map(j => ({
          title: j.title,
          location: j.location?.name,
          department: j.departments?.[0]?.name,
          id: String(j.id),
          datePosted: j.updated_at?.slice(0, 10),
          extractionMethod: 'greenhouse-api',
        }));
      },
    };
  }

  // Lever: jobs.lever.co/{company}
  if (/^jobs\.lever\.co$/i.test(url.hostname)) {
    const company = url.pathname.split('/').filter(Boolean)[0];
    if (!company) return null;
    return {
      type: 'lever',
      apiUrl: `https://api.lever.co/v0/postings/${company}?mode=json&limit=500`,
      extract: (body) => {
        const postings = body as LeverPosting[];
        if (!Array.isArray(postings)) return [];
        return postings.map(p => ({
          title: p.text,
          location: p.categories?.location,
          department: p.categories?.department,
          id: p.id,
          datePosted: p.createdAt ? new Date(p.createdAt).toISOString().slice(0, 10) : undefined,
          extractionMethod: 'lever-api',
        }));
      },
    };
  }

  // SmartRecruiters: jobs.smartrecruiters.com/{company}
  if (/^jobs\.smartrecruiters\.com$/i.test(url.hostname)) {
    const company = url.pathname.split('/').filter(Boolean)[0];
    if (!company) return null;
    return {
      type: 'smartrecruiters',
      apiUrl: `https://api.smartrecruiters.com/v1/companies/${company}/postings?limit=100&status=PUBLIC`,
      extract: (body) => {
        const data = body as SRResponse;
        return (data.content ?? []).map(j => ({
          title: j.name,
          location: [j.location?.city, j.location?.country].filter(Boolean).join(', '),
          department: j.department?.label,
          id: j.id,
          datePosted: j.releasedDate?.slice(0, 10),
          extractionMethod: 'smartrecruiters-api',
        }));
      },
    };
  }

  // Workable: {company}.workable.com
  const workableMatch = url.hostname.match(/^([^.]+)\.workable\.com$/i);
  if (workableMatch) {
    const company = workableMatch[1];
    return {
      type: 'workable',
      apiUrl: `https://apply.workable.com/api/v1/widget/accounts/${company}/jobs`,
      extract: (body) => {
        const data = body as WorkableResponse;
        return (data.jobs ?? []).map(j => ({
          title: j.title,
          location: j.location?.location_str,
          department: j.department,
          id: j.id,
          datePosted: j.created_at?.slice(0, 10),
          extractionMethod: 'workable-api',
        }));
      },
    };
  }

  return null;
}

/** Fetch CDX snapshots for an ATS API endpoint and extract jobs from each snapshot. */
async function collectFromAtsApi(
  jobs: Map<string, JobSighting>,
  ats: AtsInfo,
  startDate: string,
  endDate: string,
  maxSnapshots: number,
  delayMs: number,
): Promise<void> {
  const snapshots = await fetchCdxSnapshots({ url: ats.apiUrl, startDate, endDate, maxSnapshots });
  log.info(`Career ATS API: ${snapshots.length} snapshots`, { url: ats.apiUrl, type: ats.type });
  for (let i = 0; i < snapshots.length; i++) {
    const snap = snapshots[i];
    const date = snapshotToDate(snap.timestamp);
    log.info(`[career-api ${i + 1}/${snapshots.length}] ${date} (${ats.type})`);
    const data = await fetchArchivedJson<unknown>(snap.timestamp, snap.original);
    if (data) mergeJobs(jobs, ats.extract(data), snap);
    if (i < snapshots.length - 1) await sleep(delayMs);
  }
}

/** Merge extracted jobs into the registry, keeping the earliest sighting per title. */
function mergeJobs(
  registry: Map<string, JobSighting>,
  extracted: Partial<JobSighting>[],
  snap: CdxSnapshot,
): void {
  const date = snapshotToDate(snap.timestamp);
  const snapshotUrl = `https://web.archive.org/web/${snap.timestamp}/${snap.original}`;

  for (const raw of extracted) {
    if (!raw.title?.trim()) continue;
    const normTitle = normalizeTitle(raw.title);
    if (!normTitle) continue;

    const existing = registry.get(normTitle);
    // For career page: use datePosted if available, else snapshot date
    const effectiveDate = raw.datePosted ?? date;

    if (!existing || effectiveDate < existing.firstSeen) {
      registry.set(normTitle, {
        title: raw.title,
        normalizedTitle: normTitle,
        firstSeen: effectiveDate,
        datePosted: raw.datePosted,
        snapshotUrl,
        location: raw.location,
        department: raw.department,
        id: raw.id,
        platform: 'career_page',
        extractionMethod: raw.extractionMethod ?? 'unknown',
      });
    }
  }
}

/** Convert CDX timestamp (YYYYMMDDHHmmss) to YYYY-MM-DD. */
function snapshotToDate(ts: string): string {
  return `${ts.slice(0, 4)}-${ts.slice(4, 6)}-${ts.slice(6, 8)}`;
}

// ── ATS response type interfaces ─────────────────────────────────────────────

interface GhJob { id: number; title: string; location: { name: string }; departments: { name: string }[]; updated_at: string }
interface LeverPosting { id: string; text: string; categories: { location?: string; department?: string }; createdAt: number }
interface SRJob { id: string; name: string; location: { city?: string; country?: string }; department: { label?: string }; releasedDate?: string }
interface SRResponse { content: SRJob[] }
interface WorkableJob { id: string; title: string; location?: { location_str?: string }; department?: string; created_at?: string }
interface WorkableResponse { jobs: WorkableJob[] }

// ── HTML-based extractors ─────────────────────────────────────────────────────

function extractFromHtml(html: string, snap: CdxSnapshot): Partial<JobSighting>[] {
  const $ = load(html);
  const results: Partial<JobSighting>[] = [];

  // 1. JSON-LD JobPosting
  $('script[type="application/ld+json"]').each((_, el) => {
    try {
      const raw = $(el).html() ?? '';
      const data: unknown = JSON.parse(raw);
      const items = Array.isArray(data) ? data : [data];
      for (const item of items) {
        if (!item || typeof item !== 'object') continue;
        const obj = item as Record<string, unknown>;
        if (obj['@type'] === 'JobPosting' || (Array.isArray(obj['@type']) && (obj['@type'] as string[]).includes('JobPosting'))) {
          const title = String(obj['title'] ?? obj['name'] ?? '').trim();
          if (!title) continue;
          results.push({
            title,
            datePosted: obj['datePosted'] ? String(obj['datePosted']).slice(0, 10) : undefined,
            location: extractLocationFromJsonLd(obj),
            department: obj['occupationalCategory'] ? String(obj['occupationalCategory']) : undefined,
            extractionMethod: 'jsonld',
          });
        }
      }
    } catch { /* skip */ }
  });
  if (results.length > 0) return results;

  // 2. __NEXT_DATA__
  const nextData = extractJobsFromNextData(html, 'html');
  if (nextData.length > 0) return nextData;

  // 3. Greenhouse boards HTML: <div class="opening"><a href="/slug/jobs/ID">Title</a><span class="location">…</span></div>
  return extractFromGreenhouseBoards($);
}

function extractLocationFromJsonLd(obj: Record<string, unknown>): string | undefined {
  const loc = obj['jobLocation'];
  if (!loc) return undefined;
  const items = Array.isArray(loc) ? loc : [loc];
  const parts: string[] = [];
  for (const l of items) {
    if (typeof l === 'string') { if (l) parts.push(l); }
    else if (l && typeof l === 'object') {
      const lo = l as Record<string, unknown>;
      const addr = lo['address'] as Record<string, unknown> | undefined;
      const part = String(lo['name'] ?? addr?.['addressLocality'] ?? addr?.['addressRegion'] ?? '').trim();
      if (part) parts.push(part);
    }
  }
  return parts.length > 0 ? parts.join(', ') : undefined;
}

function extractFromGreenhouseBoards($: ReturnType<typeof load>): Partial<JobSighting>[] {
  const results: Partial<JobSighting>[] = [];
  $('div.opening').each((_, el) => {
    const a = $(el).find('a').first();
    const title = a.text().trim();
    if (!title) return;
    const location = $(el).find('span.location').first().text().trim() || undefined;
    // department: nearest preceding <h3> within parent section
    const section = $(el).closest('section');
    const department = section.find('h3').first().text().trim() || undefined;
    const href = a.attr('href') ?? '';
    const idMatch = href.match(/\/(\d+)$/);
    results.push({
      title,
      location,
      department,
      id: idMatch ? idMatch[1] : undefined,
      extractionMethod: 'greenhouse-html',
    });
  });
  return results;
}

function extractJobsFromNextData(html: string, method: string): Partial<JobSighting>[] {
  const $ = load(html);
  const script = $('script#__NEXT_DATA__').html() ?? '';
  if (!script) return [];
  try {
    const data = JSON.parse(script) as Record<string, unknown>;
    return findJobsInObject(data, method);
  } catch { return []; }
}

function findJobsInObject(obj: unknown, method: string, depth = 0): Partial<JobSighting>[] {
  if (depth > 10 || !obj || typeof obj !== 'object') return [];
  if (Array.isArray(obj)) {
    // Check if this looks like a job array
    const sample = obj[0];
    if (sample && typeof sample === 'object') {
      const s = sample as Record<string, unknown>;
      if ('title' in s || 'jobTitle' in s || 'name' in s) {
        return (obj as Record<string, unknown>[])
          .map(item => {
            const title = String(item['title'] ?? item['jobTitle'] ?? item['name'] ?? '').trim();
            if (!title) return null;
            return {
              title,
              datePosted: item['datePosted'] ? String(item['datePosted']).slice(0, 10) : (item['created_at'] ? String(item['created_at']).slice(0, 10) : undefined),
              location: item['location'] ? String(typeof item['location'] === 'object' ? (item['location'] as Record<string, unknown>)['name'] ?? '' : item['location']) : undefined,
              department: item['department'] ? String(typeof item['department'] === 'object' ? (item['department'] as Record<string, unknown>)['name'] ?? '' : item['department']) : undefined,
              extractionMethod: method,
            } as Partial<JobSighting>;
          })
          .filter((j): j is Partial<JobSighting> => j !== null);
      }
    }
    const collected: Partial<JobSighting>[] = [];
    for (const item of obj) collected.push(...findJobsInObject(item, method, depth + 1));
    return collected;
  }
  const record = obj as Record<string, unknown>;
  const collected: Partial<JobSighting>[] = [];
  for (const val of Object.values(record)) {
    if (val && typeof val === 'object') collected.push(...findJobsInObject(val, method, depth + 1));
  }
  return collected;
}
