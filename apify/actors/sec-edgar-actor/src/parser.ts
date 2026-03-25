/**
 * @module sec-edgar-actor/parser
 *
 * Queries the SEC EDGAR full-text search API (EFTS) for 10-K and 10-Q filings
 * that contain hiring/expansion language, and maps results to Signals.
 *
 * API: GET https://efts.sec.gov/LATEST/search-index?q=...&forms=10-K,10-Q
 * No auth required. SEC requests a polite User-Agent and delay between requests.
 *
 * EDGAR EFTS response shape (per hit):
 *   _source.display_names[]  — e.g. ["STRIPE INC  (STRP)  (CIK 0001820953)"]
 *   _source.file_date        — filing date "YYYY-MM-DD"
 *   _source.form             — form type "10-K" or "10-Q"
 *   _source.period_ending    — period end date
 *   _source.file_num[]       — filing numbers
 *   _source.adsh             — accession number
 */

import { Signal } from '../../../shared/types';
import { signalId } from '../../../shared/id';
import { guessDomain, sleep } from '../../../shared/utils';

const EDGAR_BASE = 'https://efts.sec.gov/LATEST/search-index';

const HIRING_PHRASES = [
  'we expect to hire',
  'expanding our team',
  'we plan to hire',
  'workforce expansion',
  'we intend to grow',
  'increase our headcount',
  'we are growing our team',
  'additional employees',
  'talent acquisition',
];

interface EdgarHitSource {
  display_names?: string[];
  file_date?: string;
  period_ending?: string;
  form?: string;
  root_forms?: string[];
  file_num?: string[];
  adsh?: string;
  biz_locations?: string[];
}

interface EdgarSearchResponse {
  hits: {
    hits: Array<{ _id: string; _source: EdgarHitSource; _score?: number }>;
    total: { value: number };
  };
}

/** Extract company name from EDGAR display_names like "STRIPE INC  (STRP)  (CIK 0001820953)" */
function parseDisplayName(raw: string): string {
  // Strip trailing (CIK ...) and (TICKER)
  return raw
    .replace(/\s*\(CIK\s+\d+\)\s*$/i, '')
    .replace(/\s*\([A-Z]{1,6}\)\s*$/i, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export async function parseEdgarFilings(
  companies: string[],
  lookbackDays: number
): Promise<Signal[]> {
  const signals: Signal[] = [];
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays);

  for (const phrase of HIRING_PHRASES) {
    const encodedPhrase = encodeURIComponent(`"${phrase}"`);
    const url = `${EDGAR_BASE}?q=${encodedPhrase}&forms=10-K,10-Q`;

    try {
      const response = await fetch(url, {
        headers: {
          'User-Agent': 'hiring-signal-engine/1.0 (research@example.com)',
          Accept: 'application/json',
        },
      });

      if (!response.ok) {
        console.warn(`EDGAR search failed for "${phrase}": ${response.status}`);
        continue;
      }

      const data = (await response.json()) as EdgarSearchResponse;
      const hits = data.hits?.hits ?? [];
      console.log(`"${phrase}": ${hits.length} hits (total: ${data.hits?.total?.value ?? '?'})`);

      for (const hit of hits) {
        const src = hit._source;
        const rawName = src.display_names?.[0];
        if (!rawName) continue;

        const entityName = parseDisplayName(rawName);
        const filingDate = src.file_date ?? src.period_ending ?? '';
        if (!filingDate) continue;

        // Client-side date filter (EDGAR doesn't reliably filter server-side)
        const filingDateObj = new Date(filingDate);
        if (filingDateObj < cutoff) continue;

        // Company name filter
        if (companies.length > 0) {
          const matches = companies.some(
            (c) =>
              entityName.toLowerCase().includes(c.toLowerCase()) ||
              c.toLowerCase().includes(entityName.toLowerCase())
          );
          if (!matches) continue;
        }

        const formType = src.form ?? src.root_forms?.[0] ?? '10-K';
        const domain = guessDomain(entityName);
        const adsh = src.adsh ?? '';
        const sourceUrl = adsh
          ? `https://www.sec.gov/Archives/edgar/data/${adsh.replace(/-/g, '/')}`
          : `https://efts.sec.gov/LATEST/search-index?q=${encodeURIComponent(hit._id)}`;

        signals.push({
          id: signalId(entityName, 'sec_filing', filingDate),
          company: entityName,
          company_domain: domain,
          signal_type: 'sec_filing',
          signal_text: `${entityName} mentioned "${phrase}" in their ${formType} filing dated ${filingDate}`,
          source_url: sourceUrl,
          date: filingDateObj.toISOString(),
          raw: {
            form_type: formType,
            file_date: filingDate,
            period_ending: src.period_ending,
            matched_phrase: phrase,
            file_num: src.file_num,
            biz_location: src.biz_locations?.[0],
            edgar_id: hit._id,
          },
        });
      }
    } catch (err) {
      console.error(`Error querying EDGAR for "${phrase}":`, err);
    }

    await sleep(300);
  }

  // Mode 2: Company-specific search
  if (companies.length > 0) {
    for (const company of companies) {
      const companySignals = await searchByCompany(company, cutoff);
      signals.push(...companySignals);
    }
  }

  // Deduplicate
  const seen = new Set<string>();
  return signals.filter((s) => {
    if (seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
}

async function searchByCompany(company: string, cutoff: Date): Promise<Signal[]> {
  const signals: Signal[] = [];
  const url = `${EDGAR_BASE}?q=${encodeURIComponent(`"${company}"`)}&forms=10-K,10-Q`;

  try {
    const response = await fetch(url, {
      headers: {
        'User-Agent': 'hiring-signal-engine/1.0 (research@example.com)',
        Accept: 'application/json',
      },
    });

    if (!response.ok) return signals;

    const data = (await response.json()) as EdgarSearchResponse;
    const hits = data.hits?.hits ?? [];

    for (const hit of hits.slice(0, 10)) {
      const src = hit._source;
      const rawName = src.display_names?.[0];
      const entityName = rawName ? parseDisplayName(rawName) : company;
      const filingDate = src.file_date ?? src.period_ending ?? '';
      if (!filingDate || new Date(filingDate) < cutoff) continue;

      const formType = src.form ?? '10-K';
      signals.push({
        id: signalId(entityName, 'sec_filing', filingDate, 'company_search'),
        company: entityName,
        company_domain: guessDomain(entityName),
        signal_type: 'sec_filing',
        signal_text: `${entityName} filed ${formType} on ${filingDate} — review for growth/hiring language`,
        source_url: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=${encodeURIComponent(company)}&type=${formType}&dateb=&owner=include&count=10`,
        date: new Date(filingDate).toISOString(),
        raw: {
          form_type: formType,
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
