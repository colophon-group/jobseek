import { gotScraping } from 'got-scraping';

export interface FetchOptions {
  url: string;
  proxyUrl?: string;
  headers?: Record<string, string>;
}

export const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export async function fetchPage(opts: FetchOptions, retries = 2): Promise<string | null> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await gotScraping({
        url: opts.url,
        proxyUrl: opts.proxyUrl,
        headers: opts.headers,
        headerGeneratorOptions: {
          browsers: ['chrome'],
          operatingSystems: ['macos', 'windows'],
          locales: ['en-US', 'de-DE'],
        },
        timeout: { request: 30_000 },
        followRedirect: true,
      });

      if (response.statusCode === 200) return response.body;

      if (response.statusCode === 429 || response.statusCode >= 500) {
        console.warn(`HTTP ${response.statusCode} for ${opts.url}, attempt ${attempt + 1}`);
        await sleep(5000 * (attempt + 1));
        continue;
      }

      return null;
    } catch (err) {
      console.warn(`Fetch error (attempt ${attempt + 1}) for ${opts.url}: ${err}`);
      if (attempt < retries) await sleep(3000 * (attempt + 1));
    }
  }
  return null;
}

/** Simple JSON fetch for public APIs (no anti-bot needed) */
export async function fetchJson<T>(url: string, timeout = 15000): Promise<T | null> {
  try {
    const resp = await fetch(url, {
      signal: AbortSignal.timeout(timeout),
      headers: { 'Accept': 'application/json', 'User-Agent': 'JobseekDiscovery/1.0' },
    });
    if (!resp.ok) return null;
    return await resp.json() as T;
  } catch {
    return null;
  }
}

/** Process items in batches with concurrency control */
export async function processInBatches<T, R>(
  items: T[],
  batchSize: number,
  fn: (item: T) => Promise<R | null>,
): Promise<R[]> {
  const results: R[] = [];
  for (let i = 0; i < items.length; i += batchSize) {
    const batch = items.slice(i, i + batchSize);
    const batchResults = await Promise.all(batch.map(fn));
    for (const r of batchResults) {
      if (r !== null) results.push(r);
    }
  }
  return results;
}
