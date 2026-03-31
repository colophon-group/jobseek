const WB = 'https://web.archive.org/web';
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

/**
 * Fetch the raw HTML of an archived page from the Wayback Machine.
 * Uses the `id_` flag to get the original, unmodified response (no Wayback toolbar injection).
 */
export async function fetchArchivedPage(timestamp: string, url: string): Promise<string | null> {
  for (let i = 0; i < 3; i++) {
    try {
      const res = await fetch(`${WB}/${timestamp}id_/${url}`, {
        signal: AbortSignal.timeout(30_000),
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
          'Accept': 'text/html,application/xhtml+xml,*/*',
          'Accept-Language': 'en-US,en;q=0.9',
        },
        redirect: 'follow',
      });
      if (res.status === 429) { await sleep(15_000 * (i + 1)); continue; }
      if (res.status === 404) return null;
      if (!res.ok) { await sleep(3_000 * (i + 1)); continue; }
      return await res.text();
    } catch { await sleep(3_000 * (i + 1)); }
  }
  return null;
}

/**
 * Fetch an archived JSON endpoint from the Wayback Machine.
 * Returns null if the response starts with HTML (Wayback error page).
 */
export async function fetchArchivedJson<T>(timestamp: string, url: string): Promise<T | null> {
  for (let i = 0; i < 3; i++) {
    try {
      const res = await fetch(`${WB}/${timestamp}id_/${url}`, {
        signal: AbortSignal.timeout(20_000),
        headers: {
          'Accept': 'application/json',
          'User-Agent': 'Mozilla/5.0 (compatible; CareerVsLinkedIn/1.0)',
        },
      });
      if (res.status === 429) { await sleep(12_000 * (i + 1)); continue; }
      if (res.status === 404) return null;
      if (!res.ok) { await sleep(2_000 * (i + 1)); continue; }
      const text = await res.text();
      if (text.trimStart().startsWith('<')) return null;
      return JSON.parse(text) as T;
    } catch { await sleep(2_000 * (i + 1)); }
  }
  return null;
}
