/**
 * Meta Careers Scraper — Apify Actor
 *
 * Strategy:
 *   1. Load metacareers.com/jobsearch with Playwright
 *   2. Intercept the GraphQL response that returns the full job listing
 *   3. For each job, optionally load the detail page to capture description
 *      (falls back gracefully if the detail page is gated)
 *
 * Output schema (Apify Dataset):
 *   url         — job detail URL
 *   jobId       — numeric ID
 *   title       — job title
 *   locations   — array of "City, ST" strings
 *   teams       — department / team names
 *   subTeams    — sub-team names
 *   description — job description (when detail page is accessible)
 *   responsibilities — responsibilities text (when accessible)
 *   qualifications   — qualifications text (when accessible)
 *   employmentType   — from JSON-LD (when accessible)
 *   datePosted       — ISO date (when accessible)
 *   validThrough     — ISO date (when accessible)
 *   company     — always "Meta"
 */

import { Actor } from 'apify';
import { log } from 'crawlee';
import { chromium, type Browser, type BrowserContext } from 'playwright';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Input {
    maxJobs?: number;
    searchQuery?: string;
    fetchDescriptions?: boolean;
}

interface GraphQLJob {
    id: string;
    title: string;
    locations: string[];
    teams: string[];
    sub_teams: string[];
    // Meta may include remote/work-type fields under various names
    remote_type?: string;
    location_type?: string;
    workplace_type?: string;
    [key: string]: unknown;
}

interface JobData {
    url: string;
    jobId: string;
    title: string;
    locations: string[];
    teams: string[];
    subTeams: string[];
    description?: string;
    responsibilities?: string;
    qualifications?: string;
    employmentType?: string;
    jobLocationType?: string;
    datePosted?: string;
    validThrough?: string;
    company: string;
}

// ---------------------------------------------------------------------------
// GraphQL intercept — captures job listing from the search page
// ---------------------------------------------------------------------------

async function captureJobListing(context: BrowserContext, maxJobs: number, searchQuery: string): Promise<GraphQLJob[]> {
    const page = await context.newPage();
    let captured: GraphQLJob[] = [];

    page.on('response', async (resp) => {
        if (!resp.url().includes('/graphql')) return;
        try {
            const text = await resp.text();
            if (!text.includes('all_jobs')) return;

            const data = JSON.parse(text) as {
                data?: { job_search_with_featured_jobs?: { all_jobs?: GraphQLJob[] } };
            };
            const jobs = data?.data?.job_search_with_featured_jobs?.all_jobs;
            if (Array.isArray(jobs) && jobs.length > captured.length) {
                captured = jobs;
                log.info(`GraphQL snapshot: ${jobs.length} jobs`);
                // Log all keys from first job once so we can see what fields Meta exposes
                if (jobs.length > 0) {
                    log.info(`GraphQL job fields: ${Object.keys(jobs[0]).join(', ')}`);
                }
            }
        } catch { /* ignore parse errors */ }
    });

    log.info('Loading https://www.metacareers.com/jobsearch …');
    await page.goto('https://www.metacareers.com/jobsearch', {
        waitUntil: 'networkidle',
        timeout: 60_000,
    });

    await page.close();

    // Filter by searchQuery if provided
    if (searchQuery) {
        const q = searchQuery.toLowerCase();
        captured = captured.filter(
            (j) =>
                j.title.toLowerCase().includes(q) ||
                j.teams.some((t) => t.toLowerCase().includes(q)) ||
                j.locations.some((l) => l.toLowerCase().includes(q)),
        );
        log.info(`After search filter "${searchQuery}": ${captured.length} jobs`);
    }

    if (maxJobs > 0) {
        captured = captured.slice(0, maxJobs);
        log.info(`Capped to ${captured.length} jobs`);
    }

    return captured;
}

// ---------------------------------------------------------------------------
// JSON-LD extraction helpers
// ---------------------------------------------------------------------------

interface JsonLdNode {
    '@type'?: string | string[];
    '@graph'?: JsonLdNode[];
    [key: string]: unknown;
}

function findJobPosting(data: unknown): Record<string, unknown> | null {
    if (Array.isArray(data)) {
        for (const item of data) {
            const r = findJobPosting(item);
            if (r) return r;
        }
        return null;
    }
    if (typeof data !== 'object' || data === null) return null;
    const obj = data as JsonLdNode;
    const t = obj['@type'];
    const ts = Array.isArray(t) ? t.join(' ') : String(t ?? '');
    if (ts.includes('JobPosting')) return obj as Record<string, unknown>;
    if (Array.isArray(obj['@graph'])) return findJobPosting(obj['@graph']);
    return null;
}

function extractFromHtml(html: string): Partial<JobData> {
    const blocks = [
        ...html.matchAll(/<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi),
    ];
    for (const [, raw] of blocks) {
        if (!raw.trim()) continue;
        try {
            const p = findJobPosting(JSON.parse(raw));
            if (!p) continue;
            // Normalise jobLocationType: schema.org uses "TELECOMMUTE" for remote;
            // Meta may also emit "REMOTE", "HYBRID", or "ON_SITE".
            const rawLocType = p['jobLocationType'] as string | undefined;
            let jobLocationType: string | undefined;
            if (rawLocType) {
                const u = rawLocType.toUpperCase();
                if (u.includes('TELECOMMUTE') || u.includes('REMOTE')) jobLocationType = 'REMOTE';
                else if (u.includes('HYBRID')) jobLocationType = 'HYBRID';
                else if (u.includes('ON') || u.includes('SITE') || u.includes('OFFICE')) jobLocationType = 'ON_SITE';
                else jobLocationType = rawLocType;
            }
            return {
                description: p['description'] as string | undefined,
                responsibilities: p['responsibilities'] as string | undefined,
                qualifications: (p['qualifications'] ?? p['educationRequirements']) as string | undefined,
                employmentType: p['employmentType'] as string | undefined,
                jobLocationType,
                datePosted: p['datePosted'] as string | undefined,
                validThrough: p['validThrough'] as string | undefined,
            };
        } catch { /* skip */ }
    }
    return {};
}

// ---------------------------------------------------------------------------
// Detail page scraper (best-effort)
// ---------------------------------------------------------------------------

/**
 * Walk a parsed GraphQL response and return the first node that looks like
 * a job-detail payload (has a non-empty "description" field).
 */
function findDetailNode(obj: unknown): Record<string, unknown> | null {
    if (typeof obj !== 'object' || obj === null) return null;
    if (Array.isArray(obj)) {
        for (const item of obj) {
            const r = findDetailNode(item);
            if (r) return r;
        }
        return null;
    }
    const rec = obj as Record<string, unknown>;
    if (typeof rec['description'] === 'string' && rec['description'].length > 20) return rec;
    for (const val of Object.values(rec)) {
        const r = findDetailNode(val);
        if (r) return r;
    }
    return null;
}

async function scrapeDetail(context: BrowserContext, url: string): Promise<Partial<JobData>> {
    const page = await context.newPage();
    let graphqlData: Partial<JobData> | null = null;

    // Primary: intercept the GraphQL response on the detail page (Meta is a SPA;
    // the description lives in a network response, not in the initial HTML).
    page.on('response', async (resp) => {
        if (!resp.url().includes('/graphql')) return;
        try {
            const text = await resp.text();
            // Quick pre-filter to avoid parsing every GraphQL response.
            if (!text.includes('description')) return;
            const parsed = JSON.parse(text);
            const node = findDetailNode(parsed);
            if (!node) return;
            const candidate: Partial<JobData> = {};
            if (typeof node['description'] === 'string') candidate.description = node['description'] as string;
            if (typeof node['responsibilities'] === 'string') candidate.responsibilities = node['responsibilities'] as string;
            if (typeof node['qualifications'] === 'string') candidate.qualifications = node['qualifications'] as string;
            if (typeof node['employment_type'] === 'string') candidate.employmentType = node['employment_type'] as string;
            if (typeof node['date_posted'] === 'string') candidate.datePosted = node['date_posted'] as string;
            if (typeof node['valid_through'] === 'string') candidate.validThrough = node['valid_through'] as string;
            // remote_type, job_location_type, or similar field names used by Meta GraphQL
            const locType = (node['remote_type'] ?? node['job_location_type'] ?? node['jobLocationType']) as string | undefined;
            if (locType) {
                const u = locType.toUpperCase();
                if (u.includes('REMOTE') || u.includes('TELECOMMUTE')) candidate.jobLocationType = 'REMOTE';
                else if (u.includes('HYBRID')) candidate.jobLocationType = 'HYBRID';
                else if (u.includes('ON') || u.includes('SITE') || u.includes('OFFICE')) candidate.jobLocationType = 'ON_SITE';
                else candidate.jobLocationType = locType;
            }
            if (candidate.description && !graphqlData) {
                graphqlData = candidate;
            }
        } catch { /* ignore */ }
    });

    try {
        const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 45_000 });
        if (!resp || !resp.ok()) return {};

        // Return GraphQL-captured data if we got it.
        if (graphqlData) return graphqlData;

        // Fallback: extract from JSON-LD in rendered HTML.
        const html = await page.content();
        if (html.length < 500 || html.includes('Not Logged In')) return {};
        return extractFromHtml(html);
    } catch {
        return graphqlData ?? {};
    } finally {
        await page.close();
    }
}

// ---------------------------------------------------------------------------
// Actor entry point
// ---------------------------------------------------------------------------

async function main() {
    await Actor.init();

    const input = ((await Actor.getInput()) ?? {}) as Input;
    const maxJobs = input.maxJobs ?? 0;
    const searchQuery = input.searchQuery ?? '';
    const fetchDescriptions = input.fetchDescriptions ?? true;

    log.info('Starting Meta Careers scraper', { maxJobs, searchQuery, fetchDescriptions });

    let browser: Browser | null = null;
    try {
        browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
        const context = await browser.newContext({
            userAgent:
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        });

        // Step 1: capture the job listing via GraphQL intercept
        const graphqlJobs = await captureJobListing(context, maxJobs, searchQuery);

        if (!graphqlJobs.length) {
            log.warning('No jobs captured from GraphQL — exiting.');
            await Actor.exit();
            return;
        }

        log.info(`Processing ${graphqlJobs.length} jobs…`);

        // Step 2: for each job, optionally fetch detail page; push to dataset
        for (let i = 0; i < graphqlJobs.length; i++) {
            const gj = graphqlJobs[i];
            const url = `https://www.metacareers.com/profile/job_details/${gj.id}`;

            // Infer jobLocationType from GraphQL listing fields or location strings
            const rawWorkplaceType = (gj.remote_type ?? gj.location_type ?? gj.workplace_type) as string | undefined;
            let listingLocType: string | undefined;
            if (rawWorkplaceType) {
                const u = rawWorkplaceType.toUpperCase();
                if (u.includes('REMOTE') || u.includes('TELECOMMUTE')) listingLocType = 'REMOTE';
                else if (u.includes('HYBRID')) listingLocType = 'HYBRID';
                else if (u.includes('ON') || u.includes('SITE') || u.includes('OFFICE')) listingLocType = 'ON_SITE';
                else listingLocType = rawWorkplaceType;
            } else {
                // Fall back: check if "Remote" appears in location strings
                const locsLower = gj.locations.map((l) => l.toLowerCase());
                if (locsLower.some((l) => l.includes('remote'))) listingLocType = 'REMOTE';
                else if (locsLower.some((l) => l.includes('hybrid'))) listingLocType = 'HYBRID';
            }

            const base: JobData = {
                url,
                jobId: gj.id,
                title: gj.title,
                locations: gj.locations,
                teams: gj.teams,
                subTeams: gj.sub_teams,
                jobLocationType: listingLocType,
                company: 'Meta',
            };

            let extra: Partial<JobData> = {};
            if (fetchDescriptions) {
                log.info(`[${i + 1}/${graphqlJobs.length}] Fetching detail: ${gj.title}`);
                extra = await scrapeDetail(context, url);
                // polite delay
                await new Promise((r) => setTimeout(r, 600));
            }

            // Determine jobLocationType: detail page > listing inference > description text > ON_SITE default
            let jobLocationType = extra.jobLocationType ?? base.jobLocationType;
            if (!jobLocationType) {
                const searchText = [
                    extra.description ?? '',
                    extra.responsibilities ?? '',
                    gj.title,
                ].join(' ').toLowerCase();
                if (searchText.includes('remote')) jobLocationType = 'REMOTE';
                else if (searchText.includes('hybrid')) jobLocationType = 'HYBRID';
                else if (gj.locations.length > 0) jobLocationType = 'ON_SITE';
            }

            const job: JobData = { ...base, ...extra, jobLocationType };
            await Actor.pushData(job);

            if (!fetchDescriptions) {
                log.info(`[${i + 1}/${graphqlJobs.length}] ${gj.title} @ ${gj.locations.join(', ')}`);
            }
        }

        log.info('All jobs pushed to dataset.');
    } finally {
        await browser?.close();
    }

    await Actor.exit();
}

main();
