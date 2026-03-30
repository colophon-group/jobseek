/**
 * Workday CDX source — discovers enterprise companies via Wayback CDX wildcard scan.
 * Workday is used by thousands of Fortune 500 and enterprise companies.
 * Pattern: *.myworkdayjobs.com (tenant subdomain = company slug)
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugToName, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

/** Extract Workday tenant from any URL pattern.
 *  Returns "{tenant}|{instance}" so the instance is preserved for URL generation.
 *  - {tenant}.myworkdayjobs.com → "{tenant}|direct"
 *  - {tenant}.wd{N}.myworkdayjobs.com → "{tenant}|wd{N}"
 */
function extractWorkdayKey(url: string): string | null {
  try {
    const host = new URL(url).hostname;
    // Match {tenant}.wd{N}.myworkdayjobs.com (real format)
    const wdMatch = host.match(/^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$/i);
    if (wdMatch) {
      const t = wdMatch[1].toLowerCase();
      const inst = wdMatch[2].toLowerCase();
      return t.length >= 2 ? `${t}|${inst}` : null;
    }
    // Match {tenant}.myworkdayjobs.com (no instance, legacy/alt)
    const directMatch = host.match(/^([a-z0-9-]+)\.myworkdayjobs\.com$/i);
    if (directMatch) {
      const t = directMatch[1].toLowerCase();
      if (t === 'www' || t.length < 2) return null;
      return `${t}|direct`;
    }
    return null;
  } catch { return null; }
}

export async function discoverFromWorkdayCdx(): Promise<CompanyDiscovery[]> {
  log.info('workday-cdx: scanning *.myworkdayjobs.com + *.*.myworkdayjobs.com via Wayback CDX...');

  // Two patterns: direct subdomain AND two-level (tenant.wdN.myworkdayjobs.com)
  // *.myworkdayjobs.com/* has 662 CDX pages (~66M entries) — use 12 pages for broader coverage
  // *.*.myworkdayjobs.com/* is the wd-instance pattern with much lower CDX density
  const [directKeys, wdKeys] = await Promise.all([
    cdxEnumerateSlugs('*.myworkdayjobs.com/*', extractWorkdayKey, 10000, 12),
    cdxEnumerateSlugs('*.*.myworkdayjobs.com/*', extractWorkdayKey, 10000, 4),
  ]);

  // Merge — prefer wd-instance keys over direct keys
  const allKeys = new Map(wdKeys);
  for (const [k, v] of directKeys) allKeys.set(k, (allKeys.get(k) ?? 0) + v);

  // Resolve the best instance per tenant: for duplicate tenants (e.g., amazon|wd1 and amazon|wd5),
  // keep the one with higher count (more CDX hits = more likely to be the active instance)
  const bestKey = new Map<string, { key: string; count: number }>();
  for (const [key, count] of allKeys) {
    const [tenant] = key.split('|');
    const existing = bestKey.get(tenant);
    if (!existing || count > existing.count) bestKey.set(tenant, { key, count });
  }

  // Build slug → count map using the best key per tenant
  const slugs = new Map<string, number>();
  for (const [tenant, { key, count }] of bestKey) {
    slugs.set(`${tenant}|${key.split('|')[1]}`, count);
  }

  log.info(`workday-cdx: found ${slugs.size} unique tenants (direct: ${directKeys.size}, wd-instance: ${wdKeys.size})`);

  // Well-known company name overrides
  const knownNames: Record<string, string> = {
    amazon: 'Amazon', microsoft: 'Microsoft', google: 'Google', apple: 'Apple',
    meta: 'Meta', netflix: 'Netflix', salesforce: 'Salesforce', oracle: 'Oracle',
    sap: 'SAP', ibm: 'IBM', hp: 'HP', dell: 'Dell', cisco: 'Cisco',
    vmware: 'VMware', adobe: 'Adobe', intuit: 'Intuit', servicenow: 'ServiceNow',
    workday: 'Workday', paypal: 'PayPal', ebay: 'eBay', uber: 'Uber',
    lyft: 'Lyft', airbnb: 'Airbnb', twitter: 'Twitter', snap: 'Snap',
    pinterest: 'Pinterest', linkedin: 'LinkedIn', zoom: 'Zoom', slack: 'Slack',
    atlassian: 'Atlassian', zendesk: 'Zendesk', hubspot: 'HubSpot',
  };

  // Generate URLs preserving the actual wd-instance
  const results = slugsToDiscoveries(
    slugs,
    (key) => {
      const [tenant, inst] = key.split('|');
      return inst === 'direct'
        ? `https://${tenant}.myworkdayjobs.com`
        : `https://${tenant}.${inst}.myworkdayjobs.com`;
    },
    'workday-cdx',
  );

  // Fix company names — keys now contain "|" separator, extract just the tenant part
  for (const r of results) {
    const tenant = r.job_board_url
      .replace(/^https?:\/\//, '')
      .replace(/\.(wd\d+\.)?myworkdayjobs\.com.*$/, '');
    r.company_name = knownNames[tenant] ?? slugToName(tenant);
  }

  return results;
}
