/**
 * Rippling ATS + Ashby ATS additional discovery paths.
 *
 * Rippling: fast-growing modern ATS/HCM used by tech scale-ups and Series B+ companies.
 * Pattern: ats.rippling.com/{company}
 *
 * Ashby (extended): boards.ashbyhq.com/{company} — alternative to jobs.ashbyhq.com
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

const RIPPLING_RESERVED = new Set([
  'www', 'api', 'app', 'jobs', 'login', 'support', 'help', 'blog',
  'admin', 'careers', 'demo', 'about', 'terms', 'privacy', 'auth', 'embed',
]);

function extractRipplingSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'ats.rippling.com') return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (!seg || RIPPLING_RESERVED.has(seg.toLowerCase()) || seg.length < 2) return null;
    // Skip API-like paths
    if (seg === 'v1' || seg === 'v2' || /^\d+$/.test(seg)) return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromRippling(): Promise<CompanyDiscovery[]> {
  log.info('rippling: CDX discovery — modern HCM/ATS used by Series B+ tech companies');

  // ats.rippling.com/* has 6 CDX pages — search both root and nested paths
  const [rootSlugs, nestedSlugs] = await Promise.all([
    cdxEnumerateSlugs('ats.rippling.com/*', extractRipplingSlug, 5000, 6),
    cdxEnumerateSlugs('ats.rippling.com/*/*', extractRipplingSlug, 5000, 4),
  ]);
  const slugs = new Map(rootSlugs);
  for (const [k, v] of nestedSlugs) slugs.set(k, (slugs.get(k) ?? 0) + v);

  log.info(`rippling: ${slugs.size} unique company slugs`);
  if (!slugs.size) return [];

  return slugsToDiscoveries(
    slugs,
    s => `https://ats.rippling.com/${s}`,
    'rippling',
  );
}

// ── Ashby extended: boards.ashbyhq.com ────────────────────────────────────────

function extractAshbyBoardsSlug(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.hostname !== 'boards.ashbyhq.com') return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    if (!seg || seg.length < 2 || seg === 'embed' || seg === 'api') return null;
    return seg.toLowerCase();
  } catch { return null; }
}

export async function discoverFromAshbyBoards(): Promise<CompanyDiscovery[]> {
  log.info('ashby-boards: CDX discovery — boards.ashbyhq.com alternative domain');

  const slugs = await cdxEnumerateSlugs(
    'boards.ashbyhq.com/*',
    extractAshbyBoardsSlug,
    3000,
  );

  log.info(`ashby-boards: ${slugs.size} companies`);
  if (!slugs.size) return [];

  return slugsToDiscoveries(
    slugs,
    s => `https://jobs.ashbyhq.com/${s}`, // canonicalize to jobs.ashbyhq.com
    'ashby',
  );
}
