import { log } from 'apify';

const WAYBACK = 'https://web.archive.org/web';

/**
 * Fetch an archived page from the Wayback Machine.
 * Uses the `id_` modifier to get raw content without toolbar injection.
 */
export async function fetchArchivedPage(timestamp: string, url: string): Promise<string | null> {
  const archiveUrl = `${WAYBACK}/${timestamp}id_/${url}`;

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(archiveUrl, {
        signal: AbortSignal.timeout(30_000),
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'text/html,application/xhtml+xml,application/json,*/*;q=0.9',
          'Accept-Language': 'en-US,en;q=0.9',
        },
        redirect: 'follow',
      });

      if (res.status === 429) {
        log.warning(`Wayback rate limited, waiting ${15 * (attempt + 1)}s`);
        await sleep(15_000 * (attempt + 1));
        continue;
      }

      if (res.status === 404) return null;

      if (!res.ok) {
        log.debug(`Wayback returned HTTP ${res.status} for ${archiveUrl}`);
        await sleep(3_000 * (attempt + 1));
        continue;
      }

      return await res.text();
    } catch (err) {
      log.debug(`Fetch error (attempt ${attempt + 1}): ${err}`);
      await sleep(3_000 * (attempt + 1));
    }
  }

  return null;
}

/**
 * Fetch an archived JSON API response from the Wayback Machine.
 */
export async function fetchArchivedJson<T>(timestamp: string, url: string): Promise<T | null> {
  const archiveUrl = `${WAYBACK}/${timestamp}id_/${url}`;

  try {
    const res = await fetch(archiveUrl, {
      signal: AbortSignal.timeout(20_000),
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (compatible; WaybackJobHistory/1.0)',
      },
    });

    if (!res.ok) return null;

    const text = await res.text();
    // Guard against Wayback returning an HTML error page instead of JSON
    if (text.trimStart().startsWith('<')) return null;

    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

function sleep(ms: number) {
  return new Promise(r => setTimeout(r, ms));
}
