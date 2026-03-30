/**
 * POST /agentic/api/hiring-cafe
 *
 * Quick hiring.cafe engagement signal for a company — no Apify actor needed.
 * Returns how many active job listings the company has on hiring.cafe, avg views,
 * avg applications, and whether engagement is low (ghost-job corroborating signal).
 *
 * Uses a cascade of strategies:
 *   1. Direct hiring.cafe API call (fast, may be blocked by Cloudflare)
 *   2. Wayback Machine archived _next/data viewjob JSON files (reliable, CDX-based)
 *   3. Wayback CDX hiring.cafe/api/search-jobs archived snapshots (slower)
 *
 * Request body:
 *   company  {string}  required — company name (e.g. "Stripe", "Rippling")
 *
 * Response:
 *   {
 *     found: boolean,
 *     activeListings: number,
 *     avgViews: number,
 *     avgApplications: number,
 *     lowEngagement: boolean,
 *     signal: string | null,
 *     strategy: string   // which strategy succeeded
 *   }
 *
 * @example
 * POST /agentic/api/hiring-cafe
 * { "company": "Rippling" }
 * → { found: true, activeListings: 12, avgViews: 3.2, avgApplications: 0, lowEngagement: true, signal: "hiring.cafe:12j avg3.2v 0a—low", strategy: "live-api" }
 */
import { type NextRequest, NextResponse } from 'next/server';

const HC_API = 'https://hiring.cafe/api/search-jobs';
const HC_COMPANIES_API = 'https://hiring.cafe/api/search-companies';
const WB = 'https://web.archive.org/web';
const CDX = 'http://web.archive.org/cdx/search/cdx';
const HC_HEADERS = { 'Content-Type': 'application/json', 'Accept': 'application/json', 'Origin': 'https://hiring.cafe', 'Referer': 'https://hiring.cafe/', 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' };

interface HCJob { viewCount?: number; applicationCount?: number; v5_processed_company_data?: { name?: string } }
interface HCCo { name?: string; company_name?: string; totalActiveListings?: number; activeListings?: number; jobCount?: number }

/** Extract the most distinctive token from a company name for fuzzy matching. */
function matchToken(name: string): string {
  const cleaned = name.toLowerCase()
    .replace(/\.(com|io|co|ai|net|org|app|dev)\b/g, ' ')
    .replace(/[.,&()'"/]/g, ' ')
    .replace(/\b(inc|llc|ltd|corp|ag|gmbh|the|group|holdings?|technologies|solutions|services|systems|software)\b/g, '')
    .replace(/\s+/g, ' ').trim();
  if (!cleaned) return name.toLowerCase().slice(0, 12);
  const words = cleaned.split(' ').filter(w => w.length >= 2);
  if (!words.length) return cleaned.slice(0, 12);
  if (words.length === 1) return words[0].slice(0, 14);
  const GENERIC = new Set(['com','io','co','ai','bank','capital','media','global','digital','cloud','data','labs','ventures','partners']);
  const ranked = [...words].sort((a, b) => b.length - a.length);
  const distinctive = ranked.find(w => !GENERIC.has(w)) ?? ranked[0];
  if (words.length === 2) return words.join('').slice(0, 14);
  return distinctive.slice(0, 14);
}

function buildSignal(jobs: HCJob[], source: string) {
  if (!jobs.length) return null;
  const avgV = jobs.reduce((s, j) => s + (j.viewCount ?? 0), 0) / jobs.length;
  const avgA = jobs.reduce((s, j) => s + (j.applicationCount ?? 0), 0) / jobs.length;
  const allZero = jobs.length >= 3 && jobs.every(j => (j.applicationCount ?? 0) === 0);
  const low = (avgV < 5 && avgA === 0) || allZero;
  return {
    found: true as const,
    activeListings: jobs.length,
    avgViews: Math.round(avgV * 10) / 10,
    avgApplications: Math.round(avgA * 10) / 10,
    lowEngagement: low,
    signal: low
      ? `hiring.cafe(${source}):${jobs.length}j avg${avgV.toFixed(1)}v ${avgA.toFixed(0)}a—low${allZero ? '(all-zero-apps)' : ''}`
      : avgA > 10 ? `hiring.cafe(${source}):${jobs.length}j avg${avgA.toFixed(0)}apps—genuine` : null,
    strategy: source,
  };
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({})) as Record<string, unknown>;
  const company = typeof body.company === 'string' ? body.company.trim() : '';
  if (!company) return NextResponse.json({ error: 'company is required' }, { status: 400 });

  const token = matchToken(company);

  // Strategy 1: direct hiring.cafe API call
  try {
    const [jobsRes, cosRes] = await Promise.all([
      fetch(HC_API, { method: 'POST', signal: AbortSignal.timeout(10_000), headers: HC_HEADERS, body: JSON.stringify({ searchQuery: company, filters: [], page: 0, pageSize: 20, sortBy: 'date' }) }).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(HC_COMPANIES_API, { method: 'POST', signal: AbortSignal.timeout(10_000), headers: HC_HEADERS, body: JSON.stringify({ searchQuery: company, filters: [], page: 0, pageSize: 10 }) }).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);

    // Check companies endpoint first
    if (cosRes && !JSON.stringify(cosRes).includes('cf-browser-verification')) {
      const cos: HCCo[] = (cosRes as { results?: HCCo[] }).results ?? [];
      const matched = cos.filter(co => (co.name ?? co.company_name ?? '').toLowerCase().includes(token));
      if (matched.length > 0) {
        const total = matched.reduce((s, co) => s + (co.totalActiveListings ?? co.activeListings ?? co.jobCount ?? 0), 0);
        return NextResponse.json({ found: true, activeListings: total, avgViews: 0, avgApplications: 0, lowEngagement: total === 0, signal: total === 0 ? `hiring.cafe(live-co):found,0listings—low` : total > 20 ? `hiring.cafe(live-co):${total}listings—genuine` : null, strategy: 'live-api-companies' });
      }
    }

    // Check jobs endpoint
    if (jobsRes && !JSON.stringify(jobsRes).includes('cf-browser-verification')) {
      const jobs: HCJob[] = ((jobsRes as { results?: HCJob[] }).results ?? []).filter(j => (j.v5_processed_company_data?.name ?? '').toLowerCase().includes(token));
      if (jobs.length > 0) {
        return NextResponse.json(buildSignal(jobs, 'live-api'));
      }
    }
  } catch { /* fall through */ }

  // Strategy 2: Wayback CDX archived _next/data viewjob JSON files
  try {
    const cdxUrl = `${CDX}?url=hiring.cafe/_next/data/*&output=json&fl=original,timestamp&filter=statuscode:200&collapse=urlkey&limit=400`;
    const cdxRes = await fetch(cdxUrl, { signal: AbortSignal.timeout(12_000) }).then(r => r.ok ? r.json() as Promise<string[][]> : null).catch(() => null);
    if (cdxRes) {
      const rows = cdxRes.slice(1).filter(r => r[0].includes('/viewjob/'));
      const matchedJobs: HCJob[] = [];
      for (const [fileUrl, ts] of rows) {
        try {
          const resp = await fetch(`${WB}/${ts}id_/${fileUrl}`, { signal: AbortSignal.timeout(12_000), headers: { Accept: 'application/json' } });
          if (!resp.ok) continue;
          const raw = await resp.arrayBuffer();
          // Handle gzip (magic bytes 0x1f 0x8b)
          const bytes = new Uint8Array(raw.slice(0, 2));
          let text: string;
          if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
            try {
              const ds = new DecompressionStream('gzip');
              const writer = ds.writable.getWriter(); writer.write(raw); writer.close();
              const chunks: Uint8Array[] = []; const reader = ds.readable.getReader();
              for (;;) { const { done, value } = await reader.read(); if (done) break; chunks.push(value); }
              text = new TextDecoder().decode(new Uint8Array(chunks.flatMap(c => [...c])));
            } catch { continue; }
          } else { text = new TextDecoder().decode(raw); }
          if (!text.trimStart().startsWith('{')) continue;
          type ND = { pageProps?: { job?: HCJob & { v5_processed_company_data?: { name?: string } } } };
          const d = JSON.parse(text) as ND;
          const job = d.pageProps?.job;
          if (!job) continue;
          if ((job.v5_processed_company_data?.name ?? '').toLowerCase().includes(token)) matchedJobs.push(job);
          if (matchedJobs.length >= 10) break;
        } catch { continue; }
      }
      if (matchedJobs.length > 0) return NextResponse.json(buildSignal(matchedJobs, 'wayback-next-data'));
    }
  } catch { /* fall through */ }

  // Strategy 3: Wayback CDX archived search-jobs API snapshots
  try {
    const fromTs = new Date(Date.now() - 180 * 86400_000).toISOString().slice(0, 10).replace(/-/g, '');
    const cdxUrl = `${CDX}?url=hiring.cafe/api/search-jobs&output=json&fl=timestamp,length&filter=statuscode:200&limit=20&from=${fromTs}&collapse=digest`;
    const snaps = await fetch(cdxUrl, { signal: AbortSignal.timeout(10_000) }).then(r => r.ok ? r.json() as Promise<string[][]> : null).catch(() => null);
    if (snaps) {
      const rows = snaps.slice(1).filter(r => parseInt(r[1]) > 5_000).sort((a, b) => b[0].localeCompare(a[0]));
      const matchedJobs: HCJob[] = [];
      for (const [ts] of rows.slice(0, 8)) {
        try {
          const resp = await fetch(`${WB}/${ts}id_/https://hiring.cafe/api/search-jobs`, { signal: AbortSignal.timeout(12_000), headers: { Accept: 'application/json' } });
          if (!resp.ok) continue;
          const text = await resp.text();
          const jobs: HCJob[] = (JSON.parse(text) as { results?: HCJob[] }).results ?? [];
          const matched = jobs.filter(j => (j.v5_processed_company_data?.name ?? '').toLowerCase().includes(token));
          if (matched.length) matchedJobs.push(...matched);
        } catch { continue; }
      }
      if (matchedJobs.length > 0) return NextResponse.json(buildSignal(matchedJobs, 'wayback-api'));
    }
  } catch { /* fall through */ }

  // Not found on hiring.cafe
  return NextResponse.json({ found: false, activeListings: 0, avgViews: 0, avgApplications: 0, lowEngagement: false, signal: null, strategy: 'none' });
}
