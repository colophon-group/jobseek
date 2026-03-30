/**
 * Workday CDX source — discovers enterprise companies via Wayback CDX wildcard scan.
 * Workday is used by thousands of Fortune 500 and enterprise companies.
 * Pattern: *.myworkdayjobs.com (tenant subdomain = company slug)
 */
import { log } from 'apify';
import { cdxEnumerateSlugs, slugToName, slugsToDiscoveries } from './cdx-subdomain.js';
import type { CompanyDiscovery } from '../types.js';

export async function discoverFromWorkdayCdx(): Promise<CompanyDiscovery[]> {
  log.info('workday-cdx: scanning *.myworkdayjobs.com via Wayback CDX...');

  const slugs = await cdxEnumerateSlugs(
    '*.myworkdayjobs.com/*',
    (url) => {
      try {
        const host = new URL(url).hostname;
        // host = "tenant.myworkdayjobs.com" — extract the tenant slug
        const tenant = host.replace(/\.myworkdayjobs\.com$/, '');
        // Skip generic/test/invalid slugs
        if (!tenant || tenant === 'www' || tenant.length < 2 || tenant.includes('.')) return null;
        return tenant.toLowerCase();
      } catch { return null; }
    },
    8000,
  );

  log.info(`workday-cdx: found ${slugs.size} unique tenants`);

  const results = slugsToDiscoveries(
    slugs,
    (slug) => `https://${slug}.myworkdayjobs.com`,
    'workday-cdx',
  );

  // Override company names: Workday tenants are often company slugs like "amazon", "microsoft"
  // slugToName already capitalises, but some well-known ones need better names
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
  for (const r of results) {
    const slug = r.job_board_url.replace('https://', '').replace('.myworkdayjobs.com', '');
    if (knownNames[slug]) r.company_name = knownNames[slug];
  }

  return results;
}
