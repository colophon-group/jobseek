/**
 * @module funding-news-actor/sources/crunchbase
 *
 * Fetches recent funding rounds from the Crunchbase API v4.
 *
 * API used: POST https://api.crunchbase.com/api/v4/searches/funding_rounds
 * Docs: https://data.crunchbase.com/docs/using-the-api
 *
 * Authentication: API key passed as query param `user_key`.
 * Pagination: cursor-based via `after_id` field in the request body.
 *
 * The function maps Crunchbase funding round entities to the shared Signal interface.
 * Domain is derived as a best-effort guess from the org's Crunchbase permalink slug.
 */

import { createHash } from 'crypto';
import { Signal } from '../../../../shared/types';

/** Shape of a single funding round entity from the Crunchbase API */
interface CrunchbaseFundingRound {
  identifier: { permalink: string; value: string };
  announced_on: string;
  investment_type: string;
  money_raised: { value_usd: number };
  funded_organization_identifier: { permalink: string; value: string };
  funded_organization_location?: Array<{ value: string }>;
  short_description?: string;
}

/** Top-level response from POST /searches/funding_rounds */
interface CrunchbaseSearchResponse {
  entities: Array<{
    identifier: { uuid: string; value: string };
    properties: CrunchbaseFundingRound;
  }>;
  count: number;
}

/**
 * Fetches funding rounds from Crunchbase that match the given filters.
 *
 * @param apiKey       - Crunchbase API key (user_key)
 * @param minAmount    - Minimum round size in USD (e.g. 1_000_000 for $1M)
 * @param roundTypes   - Array of Crunchbase investment_type slugs, e.g. ['seed', 'series_a', 'series_b']
 * @param lookbackDays - How many days back to search (filters on `announced_on`)
 * @param categories   - Optional Crunchbase category slugs to filter by (e.g. ['blockchain', 'artificial-intelligence'])
 * @returns Array of Signal objects with signal_type = 'funding'
 */
export async function parseCrunchbase(
  apiKey: string,
  minAmount: number,
  roundTypes: string[],
  lookbackDays: number,
  categories?: string[]
): Promise<Signal[]> {
  const signals: Signal[] = [];

  const startDate = new Date();
  startDate.setDate(startDate.getDate() - lookbackDays);
  const startDateStr = startDate.toISOString().split('T')[0]; // 'YYYY-MM-DD'

  const queryPredicates: Array<Record<string, unknown>> = [
    {
      type: 'predicate',
      field_id: 'announced_on',
      operator_id: 'gte',
      values: [startDateStr],
    },
    {
      type: 'predicate',
      field_id: 'investment_type',
      operator_id: 'includes',
      values: roundTypes,
    },
    {
      type: 'predicate',
      field_id: 'money_raised',
      operator_id: 'gte',
      values: [minAmount],
    },
  ];

  // Optional: filter by company categories (e.g. blockchain, artificial-intelligence)
  if (categories && categories.length > 0) {
    queryPredicates.push({
      type: 'predicate',
      field_id: 'funded_organization_categories',
      operator_id: 'includes',
      values: categories,
    });
  }

  const requestBody = {
    field_ids: [
      'identifier',
      'announced_on',
      'investment_type',
      'money_raised',
      'funded_organization_identifier',
      'funded_organization_location',
      'short_description',
    ],
    query: queryPredicates,
    order: [{ field_id: 'announced_on', sort: 'desc' }],
    limit: 100, // Crunchbase max per page
  };

  // Cursor-based pagination — Crunchbase uses `after_id` to fetch the next page
  let after: string | undefined;
  let hasMore = true;

  while (hasMore) {
    const body: Record<string, unknown> = { ...requestBody };
    if (after) body['after_id'] = after;

    const response = await fetch(
      `https://api.crunchbase.com/api/v4/searches/funding_rounds?user_key=${apiKey}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }
    );

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Crunchbase API error ${response.status}: ${errorText}`);
    }

    const data = (await response.json()) as CrunchbaseSearchResponse;

    if (!data.entities || data.entities.length === 0) {
      hasMore = false;
      break;
    }

    for (const entity of data.entities) {
      const props = entity.properties;
      const company = props.funded_organization_identifier?.value ?? 'Unknown';
      const permalink = props.funded_organization_identifier?.permalink ?? '';
      const domain = derivedomainFromPermalink(permalink);
      const announcedOn = props.announced_on;
      const investmentType = props.investment_type ?? 'unknown';
      const amountUsd = props.money_raised?.value_usd ?? 0;
      const amountFormatted = formatCurrency(amountUsd);

      const signalText = `${company} announced a ${formatRoundType(investmentType)} of ${amountFormatted}`;
      const sourceUrl = `https://www.crunchbase.com/funding_round/${entity.identifier?.value ?? permalink}`;

      // Signal id: deterministic hash so duplicate runs don't double-count
      const id = createHash('sha256')
        .update(`${company}:funding:${announcedOn}`)
        .digest('hex')
        .slice(0, 16);

      const signal: Signal = {
        id,
        company,
        company_domain: domain,
        signal_type: 'funding',
        signal_text: signalText,
        source_url: sourceUrl,
        careers_url: `https://${domain}/careers`,
        date: new Date(announcedOn).toISOString(),
        raw: {
          investment_type: investmentType,
          money_raised_usd: amountUsd,
          permalink,
          short_description: props.short_description ?? '',
        },
      };

      signals.push(signal);
    }

    // If fewer than 100 results came back, we've reached the last page
    if (data.entities.length < 100) {
      hasMore = false;
    } else {
      const lastEntity = data.entities[data.entities.length - 1];
      after = lastEntity.identifier?.value;
      if (!after) hasMore = false;
    }
  }

  return signals;
}

/**
 * Converts a Crunchbase permalink slug (e.g. "stripe") to a guessed domain ("stripe.com").
 * This is a heuristic — not guaranteed to be correct. contact-finder-actor
 * uses the domain for Hunter.io lookup, which will simply return 0 results if wrong.
 */
function derivedomainFromPermalink(permalink: string): string {
  if (!permalink) return '';
  const slug = permalink.replace(/[^a-z0-9-]/gi, '').toLowerCase();
  return `${slug}.com`;
}

/**
 * Formats a USD amount into a human-readable string.
 * e.g. 50_000_000 → "$50M", 1_200_000_000 → "$1.2B"
 */
function formatCurrency(amount: number): string {
  if (amount >= 1_000_000_000) return `$${(amount / 1_000_000_000).toFixed(1)}B`;
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(0)}M`;
  return `$${amount.toLocaleString()}`;
}

/**
 * Converts a Crunchbase investment_type slug to a human-readable label.
 * e.g. "series_c" → "Series C"
 */
function formatRoundType(type: string): string {
  return type
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}
