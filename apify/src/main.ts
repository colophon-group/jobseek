/**
 * Meta Careers Scraper — Apify Actor
 *
 * Discovers job postings from https://www.metacareers.com via sitemap,
 * then scrapes each job page using Playwright with JSON-LD extraction
 * and a DOM-based fallback.
 *
 * Output schema (Apify Dataset):
 *   url          — canonical job URL
 *   jobId        — numeric ID extracted from URL
 *   title        — job title
 *   description  — full job description (may contain HTML)
 *   locations    — array of location strings
 *   employmentType — e.g. "FULL_TIME"
 *   jobLocationType — e.g. "TELECOMMUTE"
 *   datePosted   — ISO date string
 *   validThrough — ISO date string (expiry)
 *   company      — hiring organisation name
 *   team         — department / team name (from URL slug)
 *   salary       — { currency, min, max, unit } or null
 */

import { Actor, ProxyConfiguration } from 'apify';
import { PlaywrightCrawler, RequestQueue } from 'crawlee';
import type { Page } from 'playwright';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Input {
    maxJobs?: number;
    searchQuery?: string;
    proxyConfiguration?: Parameters<typeof Actor.createProxyConfiguration>[0];
    maxConcurrency?: number;
}

interface JobData {
    url: string;
    jobId?: string;
    title?: string;
    description?: string;
    locations?: string[];
    employmentType?: string;
    jobLocationType?: string;
    datePosted?: string;
    validThrough?: string;
    company?: string;
    team?: string;
    salary?: SalaryData | null;
    extractionMethod?: 'json-ld' | 'dom';
}

interface SalaryData {
    currency?: string;
    min?: number;
    max?: number;
    unit?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SITEMAP_URL = 'https://www.metacareers.com/jobsearch/sitemap.xml';

/**
 * Cookies that allow access to metacareers.com job pages without a Facebook
 * login wall. These are generic bypass cookies; rotate them if pages 403.
 */
const META_COOKIES = [
    { name: 'datr', value: 'tNnoaBnoBZfeeR27JuTrMm1O', domain: '.metacareers.com', path: '/' },
    { name: 'ps_l', value: '1', domain: '.metacareers.com', path: '/' },
    { name: 'ps_n', value: '1', domain: '.metacareers.com', path: '/' },
] as const;

// ---------------------------------------------------------------------------
// Sitemap helpers
// ---------------------------------------------------------------------------

/** Fetch the sitemap XML and return all job detail URLs. */
async function fetchJobUrls(searchQuery: string): Promise<string[]> {
    const res = await fetch(SITEMAP_URL, {
        headers: { 'User-Agent': 'Mozilla/5.0 (compatible; ApifyBot/1.0)' },
    });
    if (!res.ok) throw new Error(`Sitemap fetch failed: ${res.status} ${res.statusText}`);

    const xml = await res.text();

    // Extract <loc> values — no need for a full XML parser
    const urls: string[] = [];
    for (const match of xml.matchAll(/<loc>\s*(https?:\/\/[^<]+)\s*<\/loc>/g)) {
        const url = match[1].trim();
        if (!url.includes('job_details')) continue;
        if (searchQuery && !url.toLowerCase().includes(searchQuery.toLowerCase())) continue;
        urls.push(url);
    }

    return urls;
}

// ---------------------------------------------------------------------------
// JSON-LD extraction
// ---------------------------------------------------------------------------

interface JsonLdBlock {
    '@type'?: string | string[];
    '@graph'?: JsonLdBlock[];
    [key: string]: unknown;
}

function findJobPosting(data: unknown): Record<string, unknown> | null {
    if (Array.isArray(data)) {
        for (const item of data) {
            const found = findJobPosting(item);
            if (found) return found;
        }
        return null;
    }
    if (typeof data !== 'object' || data === null) return null;

    const obj = data as JsonLdBlock;
    const type = obj['@type'];
    const typeStr = Array.isArray(type) ? type.join(' ') : String(type ?? '');
    if (typeStr.includes('JobPosting')) return obj as Record<string, unknown>;

    if (Array.isArray(obj['@graph'])) return findJobPosting(obj['@graph']);
    return null;
}

/** Extract all <script type="application/ld+json"> blocks and find a JobPosting. */
async function extractJsonLd(page: Page): Promise<Record<string, unknown> | null> {
    const scripts = await page.$$eval(
        'script[type="application/ld+json"]',
        (els) => els.map((el) => el.textContent ?? ''),
    );

    for (const raw of scripts) {
        if (!raw.trim()) continue;
        try {
            const data = JSON.parse(raw) as unknown;
            const posting = findJobPosting(data);
            if (posting) return posting;
        } catch {
            // Attempt to repair common control-char issues
            try {
                const cleaned = raw.replace(/[\x00-\x1F]/g, (c) =>
                    c === '\n' ? '\\n' : c === '\r' ? '\\r' : c === '\t' ? '\\t' : '',
                );
                const data = JSON.parse(cleaned) as unknown;
                const posting = findJobPosting(data);
                if (posting) return posting;
            } catch {
                // ignore
            }
        }
    }
    return null;
}

function parseLocations(jobLocation: unknown): string[] | undefined {
    if (!jobLocation) return undefined;
    const items = Array.isArray(jobLocation) ? jobLocation : [jobLocation];
    const locations: string[] = [];

    for (const loc of items) {
        if (typeof loc === 'string') {
            locations.push(loc);
            continue;
        }
        if (typeof loc !== 'object' || loc === null) continue;
        const l = loc as Record<string, unknown>;

        const name = l['name'];
        if (typeof name === 'string' && name) {
            locations.push(name);
            continue;
        }

        const address = l['address'];
        if (typeof address === 'object' && address !== null) {
            const a = address as Record<string, unknown>;
            const parts = ['addressLocality', 'addressRegion', 'addressCountry']
                .map((k) => (typeof a[k] === 'string' ? a[k] : (a[k] as Record<string, unknown>)?.['name']))
                .filter(Boolean) as string[];
            if (parts.length) locations.push(parts.join(', '));
        }
    }
    return locations.length ? locations : undefined;
}

function parseSalary(baseSalary: unknown): SalaryData | null {
    if (typeof baseSalary !== 'object' || baseSalary === null) return null;
    const bs = baseSalary as Record<string, unknown>;
    const currency = typeof bs['currency'] === 'string' ? bs['currency'] : undefined;
    const value = bs['value'];

    if (typeof value === 'number') return { currency, min: value, max: value };
    if (typeof value === 'object' && value !== null) {
        const v = value as Record<string, unknown>;
        return {
            currency,
            min: typeof v['minValue'] === 'number' ? v['minValue'] : undefined,
            max: typeof v['maxValue'] === 'number' ? v['maxValue'] : undefined,
            unit: typeof v['unitText'] === 'string' ? v['unitText'].toLowerCase() : undefined,
        };
    }
    return null;
}

function jsonLdToJobData(posting: Record<string, unknown>, url: string): Partial<JobData> {
    return {
        title: (posting['title'] ?? posting['name']) as string | undefined,
        description: posting['description'] as string | undefined,
        locations: parseLocations(posting['jobLocation']) ?? undefined,
        employmentType: posting['employmentType'] as string | undefined,
        jobLocationType: posting['jobLocationType'] as string | undefined,
        datePosted: posting['datePosted'] as string | undefined,
        validThrough: posting['validThrough'] as string | undefined,
        company:
            typeof posting['hiringOrganization'] === 'object' && posting['hiringOrganization'] !== null
                ? ((posting['hiringOrganization'] as Record<string, unknown>)['name'] as string | undefined)
                : typeof posting['hiringOrganization'] === 'string'
                  ? posting['hiringOrganization']
                  : 'Meta',
        salary: parseSalary(posting['baseSalary']),
        extractionMethod: 'json-ld',
    };
}

// ---------------------------------------------------------------------------
// DOM extraction fallback
// ---------------------------------------------------------------------------

/**
 * Minimal DOM scrape: grab the <h1> title and the description block that
 * appears between "Apply now" and "Apply for this job" landmark texts.
 */
async function extractDom(page: Page): Promise<Partial<JobData>> {
    // Remove aria-hidden so hidden content becomes accessible
    await page.evaluate(() => {
        document.querySelectorAll<Element>('[aria-hidden]').forEach((el) =>
            el.removeAttribute('aria-hidden'),
        );
    });

    const title = await page.$eval('h1', (el) => el.textContent?.trim()).catch(() => undefined);

    const description = await page
        .evaluate((): string | undefined => {
            // Walk the text nodes looking for the block between "Apply now" and "Apply for this job"
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const parts: string[] = [];
            let capturing = false;
            let node: Node | null;

            while ((node = walker.nextNode())) {
                const text = node.textContent?.trim() ?? '';
                if (!capturing && text === 'Apply now') {
                    capturing = true;
                    continue;
                }
                if (capturing && text === 'Apply for this job') break;
                if (capturing && text) parts.push(text);
            }
            return parts.length ? parts.join('\n') : undefined;
        })
        .catch(() => undefined);

    // Also try extracting from common description container selectors
    const descriptionHtml =
        description ??
        (await page
            .$eval('[data-testid="job-description"], .job-description, [class*="jobDescription"]', (el) =>
                el.innerHTML.trim(),
            )
            .catch(() => undefined));

    return { title, description: descriptionHtml, extractionMethod: 'dom' };
}

// ---------------------------------------------------------------------------
// URL metadata helpers
// ---------------------------------------------------------------------------

/** Pull the numeric job ID from the URL path. */
function extractJobId(url: string): string | undefined {
    const m = url.match(/\/(\d{15,})/);
    return m ? m[1] : undefined;
}

/** Guess team/department from the URL slug segment. */
function extractTeam(url: string): string | undefined {
    // e.g. /jobs/2345678901234567/software-engineer-ads-backend/
    const m = url.match(/\/jobs\/\d+\/([^/?#]+)/);
    if (!m) return undefined;
    return m[1]
        .replace(/-/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Actor entry point
// ---------------------------------------------------------------------------

await Actor.init();

const input = ((await Actor.getInput()) ?? {}) as Input;
const maxJobs = input.maxJobs ?? 0;
const searchQuery = input.searchQuery ?? '';
const maxConcurrency = Math.max(1, Math.min(input.maxConcurrency ?? 3, 10));

// Set up proxy
let proxyConfiguration: ProxyConfiguration | undefined;
if (input.proxyConfiguration) {
    proxyConfiguration = await Actor.createProxyConfiguration(input.proxyConfiguration);
}

// Discover job URLs from sitemap
Actor.log.info('Fetching Meta careers sitemap…', { sitemapUrl: SITEMAP_URL });
let jobUrls = await fetchJobUrls(searchQuery);
Actor.log.info(`Found ${jobUrls.length} job URLs`, { filtered: !!searchQuery });

if (maxJobs > 0) {
    jobUrls = jobUrls.slice(0, maxJobs);
    Actor.log.info(`Capped to ${jobUrls.length} jobs`);
}

if (!jobUrls.length) {
    Actor.log.warning('No job URLs matched — exiting.');
    await Actor.exit();
}

// Enqueue all job URLs
const requestQueue = await RequestQueue.open();
for (const url of jobUrls) {
    await requestQueue.addRequest({ url });
}

// Crawl
const crawler = new PlaywrightCrawler({
    requestQueue,
    proxyConfiguration,
    maxConcurrency,
    // Give each page up to 90 s to load (metacareers is slow)
    navigationTimeoutSecs: 90,
    requestHandlerTimeoutSecs: 120,

    launchContext: {
        launchOptions: {
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox'],
        },
    },

    preNavigationHooks: [
        async ({ page }) => {
            // Inject bypass cookies before navigating
            await page.context().addCookies(
                META_COOKIES.map((c) => ({
                    ...c,
                    expires: -1,
                    httpOnly: false,
                    secure: true,
                    sameSite: 'Lax' as const,
                })),
            );
        },
    ],

    async requestHandler({ request, page }) {
        const url = request.url;
        Actor.log.info('Scraping', { url });

        // Wait for the page to settle
        await page.waitForLoadState('networkidle', { timeout: 30_000 }).catch(() => {
            Actor.log.debug('networkidle timeout, continuing anyway', { url });
        });

        const base: JobData = {
            url,
            jobId: extractJobId(url),
            team: extractTeam(url),
        };

        // --- Attempt 1: JSON-LD ---
        const posting = await extractJsonLd(page);
        if (posting) {
            const data: JobData = { ...base, ...jsonLdToJobData(posting, url) };
            if (data.title) {
                Actor.log.info('Extracted via JSON-LD', { title: data.title });
                await Actor.pushData(data);
                return;
            }
        }

        // --- Attempt 2: DOM fallback ---
        Actor.log.debug('JSON-LD extraction failed, trying DOM fallback', { url });
        const domData = await extractDom(page);
        const data: JobData = { ...base, ...domData };

        if (data.title || data.description) {
            Actor.log.info('Extracted via DOM', { title: data.title });
            await Actor.pushData(data);
        } else {
            Actor.log.warning('No data extracted', { url });
            // Still push a stub so operators can investigate
            await Actor.pushData({ ...base, extractionMethod: 'none' });
        }
    },

    failedRequestHandler({ request, error }) {
        Actor.log.error('Request failed', { url: request.url, error: String(error) });
    },
});

await crawler.run();

Actor.log.info('Scraping complete.');
await Actor.exit();
