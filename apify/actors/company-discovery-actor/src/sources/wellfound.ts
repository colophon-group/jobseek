/**
 * Wellfound (formerly AngelList Talent) CDX discovery.
 * Largest startup job board — tens of thousands of companies.
 * Pattern: wellfound.com/company/{slug}/jobs
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugToName, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

const WF_RESERVED = new Set(['www', 'api', 'app', 'blog', 'login', 'signup', 'help', 'about', 'press', 'terms', 'privacy', 'jobs', 'companies', 'talent', 'lists', 'today', 'slack']);

function extractWellfoundSlug(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (!['wellfound.com', 'www.wellfound.com', 'angel.co', 'www.angel.co'].includes(parsed.hostname)) return null;
    const parts = parsed.pathname.split('/').filter(Boolean);
    // /company/{slug}/jobs or /company/{slug}
    if (parts[0] !== 'company' && parts[0] !== 'companies') return null;
    const slug = parts[1];
    if (!slug || WF_RESERVED.has(slug.toLowerCase()) || slug.length < 2) return null;
    return slug.toLowerCase();
  } catch { return null; }
}

export async function discoverFromWellfound(): Promise<CompanyDiscovery[]> {
  log.info('wellfound: CDX discovery — AngelList Talent startup job board');

  // wellfound.com/company/* has 8 CDX pages — use 8 pages for full coverage
  const [wf, wfBroad, al] = await Promise.all([
    cdxEnumerateSlugs('wellfound.com/company/*/jobs', extractWellfoundSlug, 10000, 6),
    cdxEnumerateSlugs('wellfound.com/company/*', extractWellfoundSlug, 10000, 8),
    cdxEnumerateSlugs('angel.co/company/*/jobs', extractWellfoundSlug, 5000, 4),
  ]);

  const merged = new Map(wf);
  for (const [k, v] of wfBroad) merged.set(k, (merged.get(k) ?? 0) + v);
  for (const [k, v] of al) merged.set(k, (merged.get(k) ?? 0) + v);

  log.info(`wellfound: ${merged.size} unique companies`);
  if (!merged.size) return [];

  const results = slugsToDiscoveries(
    merged,
    (slug) => `https://wellfound.com/company/${slug}/jobs`,
    'wellfound',
  );

  // Better name formatting for hyphenated startup names
  for (const r of results) {
    const slug = r.job_board_url.replace('https://wellfound.com/company/', '').replace('/jobs', '');
    // Remove trailing version suffixes like -1, -2
    r.company_name = slugToName(slug.replace(/-\d+$/, ''));
  }

  return results;
}
