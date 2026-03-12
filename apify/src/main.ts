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
            return {
                description: p['description'] as string | undefined,
                responsibilities: p['responsibilities'] as string | undefined,
                qualifications: (p['qualifications'] ?? p['educationRequirements']) as string | undefined,
                employmentType: p['employmentType'] as string | undefined,
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

async function scrapeDetail(context: BrowserContext, url: string): Promise<Partial<JobData>> {
    const page = await context.newPage();
    try {
        const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
        if (!resp || !resp.ok()) return {};

        const html = await page.content();
        if (html.length < 500 || html.includes('Not Logged In')) return {};

        return extractFromHtml(html);
    } catch {
        return {};
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
    const fetchDescriptions = input.fetchDescriptions ?? false;

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

            const base: JobData = {
                url,
                jobId: gj.id,
                title: gj.title,
                locations: gj.locations,
                teams: gj.teams,
                subTeams: gj.sub_teams,
                company: 'Meta',
            };

            let extra: Partial<JobData> = {};
            if (fetchDescriptions) {
                log.info(`[${i + 1}/${graphqlJobs.length}] Fetching detail: ${gj.title}`);
                extra = await scrapeDetail(context, url);
                // polite delay
                await new Promise((r) => setTimeout(r, 600));
            }

            const job: JobData = { ...base, ...extra };
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
