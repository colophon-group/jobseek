import { Actor } from 'apify';
import { log } from 'crawlee';
import { chromium, type Browser, type BrowserContext, type Response } from 'playwright';

interface Input {
    maxJobs?: number;
    searchQuery?: string;
    locationQuery?: string;
    fetchDescriptions?: boolean;
    maxWaitForJobsSecs?: number;
    proxyConfiguration?: {
        useApifyProxy?: boolean;
        apifyProxyGroups?: string[];
        proxyUrls?: string[];
    };
}

interface TeslaJob {
    jobId: string;
    title: string;
    url: string;
    locations: string[];
    teams: string[];
    employmentType?: string;
    description?: string;
    requirements?: string[];
    postedAt?: string;
    company: 'Tesla';
    source: string;
}

function text(v: unknown): string | undefined {
    if (typeof v === 'string') {
        const t = v.trim();
        return t.length ? t : undefined;
    }
    if (typeof v === 'number') return String(v);
    return undefined;
}

function normalizeLocation(raw: Record<string, unknown>): string[] {
    const candidates: string[] = [];
    const direct = [raw.location, raw.city, raw.state, raw.country, raw.region]
        .map(text)
        .filter(Boolean) as string[];

    if (direct.length) candidates.push(direct.join(', '));

    const nestedLocation = raw.location as Record<string, unknown> | undefined;
    if (nestedLocation && typeof nestedLocation === 'object') {
        const nested = [nestedLocation.city, nestedLocation.state, nestedLocation.country, nestedLocation.name]
            .map(text)
            .filter(Boolean) as string[];
        if (nested.length) candidates.push(nested.join(', '));
    }

    const all = [...new Set(candidates.map((x) => x.replace(/\s+/g, ' ').trim()).filter(Boolean))];
    return all.length ? all : ['Unknown'];
}

function splitListField(value: unknown): string[] {
    if (!value) return [];
    if (Array.isArray(value)) {
        return value
            .map(text)
            .filter(Boolean) as string[];
    }
    const s = text(value);
    if (!s) return [];
    return s
        .split(/\||,|\u2022|\n|\//g)
        .map((x) => x.trim())
        .filter(Boolean);
}

function deriveUrl(jobId: string, maybeUrl?: string): string {
    if (maybeUrl && /^https?:\/\//.test(maybeUrl)) return maybeUrl;
    if (maybeUrl && maybeUrl.startsWith('/')) return `https://www.tesla.com${maybeUrl}`;
    return `https://www.tesla.com/careers/search/job/${jobId}`;
}

function toJob(raw: Record<string, unknown>): TeslaJob | null {
    const jobId = text(raw.id ?? raw.job_id ?? raw.req_id ?? raw.requisitionId ?? raw.external_id);
    const title = text(raw.title ?? raw.name ?? raw.job_title);
    if (!jobId || !title) return null;

    const url = deriveUrl(jobId, text(raw.url ?? raw.absolute_url ?? raw.path));
    const locations = normalizeLocation(raw);
    const teams = splitListField(raw.team ?? raw.department ?? raw.dept ?? raw.business_unit ?? raw.organization);

    return {
        jobId,
        title,
        url,
        locations,
        teams,
        employmentType: text(raw.employment_type ?? raw.type ?? raw.schedule),
        description: text(raw.description ?? raw.job_description ?? raw.summary),
        requirements: splitListField(raw.qualifications ?? raw.requirements),
        postedAt: text(raw.date_posted ?? raw.created_at ?? raw.postedAt),
        company: 'Tesla',
        source: 'tesla.com',
    };
}

function collectPossibleJobs(payload: unknown): TeslaJob[] {
    const out: TeslaJob[] = [];

    function walk(node: unknown): void {
        if (!node) return;
        if (Array.isArray(node)) {
            for (const item of node) walk(item);
            return;
        }
        if (typeof node !== 'object') return;

        const rec = node as Record<string, unknown>;
        const maybe = toJob(rec);
        if (maybe) out.push(maybe);

        for (const value of Object.values(rec)) {
            if (typeof value === 'object' && value !== null) walk(value);
        }
    }

    walk(payload);

    const dedup = new Map<string, TeslaJob>();
    for (const j of out) dedup.set(j.jobId, j);
    return [...dedup.values()];
}

async function maybeParseJsonResponse(resp: Response): Promise<TeslaJob[]> {
    try {
        const ct = resp.headers()['content-type'] ?? '';
        const url = resp.url();
        if (!url.includes('tesla.com')) return [];

        const looksLikeJobsEndpoint =
            url.includes('/api/') ||
            url.includes('/graphql') ||
            url.includes('/search') ||
            ct.includes('application/json');

        if (!looksLikeJobsEndpoint) return [];

        const body = await resp.text();
        if (!body || body.length < 2) return [];
        const parsed = JSON.parse(body) as unknown;
        return collectPossibleJobs(parsed);
    } catch {
        return [];
    }
}

async function extractFromPageScripts(context: BrowserContext): Promise<TeslaJob[]> {
    const page = await context.newPage();
    await page.goto('https://www.tesla.com/careers/search/?site=US', {
        waitUntil: 'domcontentloaded',
        timeout: 90_000,
    });

    const scripts = await page.locator('script').allTextContents();
    const found: TeslaJob[] = [];

    for (const raw of scripts) {
        const textBlock = raw.trim();
        if (!textBlock) continue;
        if (!textBlock.includes('job') && !textBlock.includes('career') && !textBlock.includes('requisition')) continue;

        try {
            const parsed = JSON.parse(textBlock) as unknown;
            found.push(...collectPossibleJobs(parsed));
        } catch {
            // many script tags are JS, not JSON
        }
    }

    await page.close();
    const dedup = new Map(found.map((j) => [j.jobId, j]));
    return [...dedup.values()];
}

function applyFilters(jobs: TeslaJob[], input: Input): TeslaJob[] {
    let filtered = jobs;

    if (input.searchQuery?.trim()) {
        const q = input.searchQuery.toLowerCase();
        filtered = filtered.filter((j) => {
            const blob = `${j.title} ${j.teams.join(' ')} ${j.description ?? ''}`.toLowerCase();
            return blob.includes(q);
        });
    }

    if (input.locationQuery?.trim()) {
        const q = input.locationQuery.toLowerCase();
        filtered = filtered.filter((j) => j.locations.some((loc) => loc.toLowerCase().includes(q)));
    }

    const maxJobs = Math.max(0, input.maxJobs ?? 0);
    if (maxJobs > 0) filtered = filtered.slice(0, maxJobs);

    return filtered;
}

await Actor.init();

const input = (await Actor.getInput<Input>()) ?? {};
const maxWaitMs = Math.max(5, input.maxWaitForJobsSecs ?? 45) * 1000;
const proxyConfiguration = await Actor.createProxyConfiguration(input.proxyConfiguration ?? {
    useApifyProxy: true,
    apifyProxyGroups: ['RESIDENTIAL'],
});
const proxyInfo = await proxyConfiguration?.newProxyInfo();

let browser: Browser | undefined;

try {
    browser = await chromium.launch({ headless: true });

    const context = await browser.newContext({
        proxy: proxyInfo?.url ? { server: proxyInfo.url } : undefined,
        userAgent:
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        locale: 'en-US',
    });

    const collected = new Map<string, TeslaJob>();

    context.on('response', async (resp) => {
        const jobs = await maybeParseJsonResponse(resp);
        if (!jobs.length) return;
        for (const j of jobs) collected.set(j.jobId, j);
        log.info(`Captured ${jobs.length} Tesla jobs from ${resp.url()}`);
    });

    const page = await context.newPage();
    log.info('Opening Tesla Careers search page…');
    await page.goto('https://www.tesla.com/careers/search/?site=US', {
        waitUntil: 'domcontentloaded',
        timeout: 90_000,
    });

    const started = Date.now();
    while (Date.now() - started < maxWaitMs) {
        if (collected.size >= (input.maxJobs && input.maxJobs > 0 ? input.maxJobs : 50)) break;
        await page.waitForTimeout(1_000);
    }

    await page.close();

    if (collected.size === 0) {
        log.warning('No jobs captured from network responses; trying inline script extraction…');
        const fallbackJobs = await extractFromPageScripts(context);
        for (const j of fallbackJobs) collected.set(j.jobId, j);
    }

    const jobs = applyFilters([...collected.values()], input);

    for (const job of jobs) {
        await Actor.pushData(job);
    }

    log.info(`Done. Pushed ${jobs.length} Tesla jobs.`);
} finally {
    await browser?.close();
    await Actor.exit();
}
