/**
 * @module sec-edgar-actor/parser
 *
 * Queries the SEC EDGAR full-text search API for 10-K and 10-Q filings
 * that contain hiring/expansion language, and maps results to Signals.
 *
 * API used:
 *   GET https://efts.sec.gov/LATEST/search-index
 *   (EDGAR's Elasticsearch-based full-text search, no auth required)
 *   SEC requests a polite User-Agent header and a delay between requests.
 *
 * Two search modes:
 *   1. Phrase search — queries each phrase in HIRING_PHRASES across all filers
 *   2. Company search — looks up recent filings for specific named companies
 *
 * Why SEC filings?
 *   10-K/10-Q language like "we plan to hire", "expanding our team", or
 *   "new data center" is a legally required disclosure — unlike PR fluff,
 *   it reflects actual business intent. Finding this language in a filing
 *   typically means headcount growth is 1–3 quarters out.
 *
 * Rate limiting:
 *   EDGAR requests polite usage. A 300ms sleep is inserted between each
 *   phrase query to avoid hammering the endpoint.
 */

import { Signal } from '../../../shared/types';
import { signalId } from '../../../shared/id';
import { guessDomain, sleep } from '../../../shared/utils';

const EDGAR_BASE = 'https://efts.sec.gov/LATEST/search-index';

/**
 * Phrases to search for in SEC filings.
 * These are chosen because they appear in growth/hiring disclosures
 * rather than standard boilerplate text.
 */
const HIRING_PHRASES = [
  'we expect to hire',
  'we are scaling',
  'expanding our team',
  'new data center',
  'headcount',
  'we plan to hire',
  'workforce expansion',
  'talent acquisition',
  'we intend to grow',
  'increase our headcount',
  'additional employees',
  'we are growing our team',
];

/** Shape of a single hit from the EDGAR full-text search response */
interface EdgarSearchHit {
  _id: string;
  _source: {
    period_of_report?: string;
    file_date?: string;
    entity_name?: string;
    file_num?: string;
    form_type?: string;
    biz_location?: string;
    inc_states?: string;
  };
  _score?: number;
}

/** Top-level EDGAR search response */
interface EdgarSearchResponse {
  hits: {
    hits: EdgarSearchHit[];
    total: { value: number; relation: string };
  };
}

/** Response shape from EDGAR company name search */
interface EdgarCompanySearchResponse {
  hits: {
    hits: Array<{
      _source: {
        entity_name: string;
        file_date: string;
        period_of_report: string;
        form_type: string;
        file_num: string;
      };
    }>;
  };
}

/**
 * Searches SEC EDGAR for 10-K/10-Q filings containing hiring/expansion language.
 *
 * @param companies    - Optional list of company names to filter results to.
 *                       If empty, returns signals for all companies mentioning the phrases.
 * @param lookbackDays - How many days back to search (`startdt` filter on EDGAR)
 * @returns Deduplicated array of Signal objects with signal_type = 'sec_filing'
 */
export async function parseEdgarFilings(
  companies: string[],
  lookbackDays: number
): Promise<Signal[]> {
  const signals: Signal[] = [];

  const startDate = new Date();
  startDate.setDate(startDate.getDate() - lookbackDays);
  const startDateStr = startDate.toISOString().split('T')[0];

  // --- Mode 1: Phrase-level search across all filers ---
  for (const phrase of HIRING_PHRASES) {
    const encodedPhrase = encodeURIComponent(`"${phrase}"`);
    const url = `${EDGAR_BASE}?q=${encodedPhrase}&dateRange=custom&startdt=${startDateStr}&forms=10-K,10-Q&hits.hits.total.value=1&hits.hits._source=period_of_report,entity_name,file_date,form_type,file_num&hits.hits.highlight.body=true`;

    try {
      const response = await fetch(url, {
        headers: {
          // EDGAR's fair-use policy requires a descriptive User-Agent
          'User-Agent': 'hiring-signal-engine/1.0 (research@example.com)',
          Accept: 'application/json',
        },
      });

      if (!response.ok) {
        console.warn(`EDGAR search failed for phrase "${phrase}": ${response.status}`);
        continue;
      }

      const data = (await response.json()) as EdgarSearchResponse;
      const hits = data.hits?.hits ?? [];

      for (const hit of hits) {
        const src = hit._source;
        const entityName = src.entity_name ?? 'Unknown Company';

        // If caller specified a company filter, skip non-matching filers
        if (companies.length > 0) {
          const matchesCompany = companies.some((c) =>
            entityName.toLowerCase().includes(c.toLowerCase()) ||
            c.toLowerCase().includes(entityName.toLowerCase())
          );
          if (!matchesCompany) continue;
        }

        const filingDate = src.file_date ?? src.period_of_report ?? new Date().toISOString().split('T')[0];
        const formType = src.form_type ?? 'SEC Filing';
        const domain = guessDomain(entityName);

        const id = signalId(entityName, 'sec_filing', filingDate);

        const signalText = `${entityName} mentioned "${phrase}" in their ${formType} filing dated ${filingDate}`;
        const sourceUrl = buildEdgarFilingUrl(hit._id, src.file_num ?? '');

        const signal: Signal = {
          id,
          company: entityName,
          company_domain: domain,
          signal_type: 'sec_filing',
          signal_text: signalText,
          source_url: sourceUrl,
          date: new Date(filingDate).toISOString(),
          raw: {
            form_type: formType,
            period_of_report: src.period_of_report,
            file_date: src.file_date,
            matched_phrase: phrase,
            file_num: src.file_num,
            edgar_id: hit._id,
          },
        };

        signals.push(signal);
      }
    } catch (err) {
      console.error(`Error querying EDGAR for phrase "${phrase}":`, err);
    }

    // Polite delay — SEC asks crawlers to be gentle
    await sleep(300);
  }

  // --- Mode 2: Company-specific search (when companies list is provided) ---
  // This mode finds *any* recent filing from the named companies, not just
  // those containing our phrases — useful as a broader sweep.
  if (companies.length > 0) {
    for (const company of companies) {
      const companySignals = await searchByCompany(company, startDateStr);
      signals.push(...companySignals);
    }
  }

  // Deduplicate by id before returning
  const seen = new Set<string>();
  return signals.filter((s) => {
    if (seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
}

/**
 * Fetches recent 10-K/10-Q filings for a specific named company.
 * Returns up to 5 most recent filings as signals, without phrase filtering.
 * Used as a broader sweep when the caller is specifically interested in a company.
 *
 * @param company      - Company name to search for
 * @param startDateStr - ISO date string 'YYYY-MM-DD' — earliest filing date to include
 */
async function searchByCompany(company: string, startDateStr: string): Promise<Signal[]> {
  const signals: Signal[] = [];
  const encodedCompany = encodeURIComponent(company);

  const url = `https://efts.sec.gov/LATEST/search-index?q=%22${encodedCompany}%22&dateRange=custom&startdt=${startDateStr}&forms=10-K,10-Q`;

  try {
    const response = await fetch(url, {
      headers: {
        'User-Agent': 'hiring-signal-engine/1.0 (research@example.com)',
        Accept: 'application/json',
      },
    });

    if (!response.ok) return signals;

    const data = (await response.json()) as EdgarCompanySearchResponse;
    const hits = data.hits?.hits ?? [];

    // Cap at 5 to avoid flooding the signals dataset with low-quality results
    for (const hit of hits.slice(0, 5)) {
      const src = hit._source;
      if (!src) continue;

      const filingDate = src.file_date ?? src.period_of_report ?? '';
      const formType = src.form_type ?? 'SEC Filing';
      const entityName = src.entity_name ?? company;
      const domain = guessDomain(entityName);

      const id = signalId(entityName, 'sec_filing', filingDate, 'company_search');

      signals.push({
        id,
        company: entityName,
        company_domain: domain,
        signal_type: 'sec_filing',
        signal_text: `${entityName} filed ${formType} on ${filingDate} — review for growth/hiring language`,
        source_url: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=${encodedCompany}&type=${formType}&dateb=&owner=include&count=10`,
        date: new Date(filingDate).toISOString(),
        raw: {
          form_type: formType,
          period_of_report: src.period_of_report,
          file_date: filingDate,
          search_term: company,
        },
      });
    }
  } catch (err) {
    console.error(`Error searching EDGAR for company "${company}":`, err);
  }

  return signals;
}

/**
 * Builds a link to the EDGAR filing document.
 * Tries to construct a direct archive URL from the EDGAR internal ID;
 * falls back to a company search URL if the ID isn't well-formed.
 */
function buildEdgarFilingUrl(edgarId: string, fileNum: string): string {
  if (edgarId) {
    return `https://efts.sec.gov/LATEST/search-index?q=${encodeURIComponent(edgarId)}`;
  }
  return `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&filenum=${fileNum}&type=10-K&dateb=&owner=include&count=10`;
}

