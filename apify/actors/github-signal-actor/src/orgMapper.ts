/**
 * @module github-signal-actor/orgMapper
 *
 * Resolves a company name (e.g. "Stripe") to its GitHub organization handle (e.g. "stripe").
 *
 * Resolution strategy (in order):
 *   1. Try common slug variants of the company name directly as org handles
 *      (e.g. "stripe", "stripe-inc", "stripe_inc", "stripe" first word only)
 *   2. Fall back to GitHub's search API with type:org filter
 *   3. Use Levenshtein distance ≤ 2 to accept near-matches from search results
 *
 * This is called by github-signal-actor/main.ts when the input contains a company
 * name with spaces or >39 characters (GitHub org handles can't contain spaces and
 * are max 39 chars).
 *
 * If a company name is already a valid org handle (no spaces, ≤39 chars), main.ts
 * skips this resolver and uses it directly.
 */

import { Octokit } from '@octokit/rest';

/**
 * Resolves a company name to a GitHub organization handle.
 *
 * @param companyName - Human-readable company name, e.g. "Stripe Inc." or "Databricks"
 * @param token       - GitHub personal access token (increases rate limit from 60 to 5000 req/hr)
 * @returns The GitHub org handle (e.g. "stripe") or null if no confident match found
 */
export async function resolveGithubOrg(
  companyName: string,
  token: string
): Promise<string | null> {
  const octokit = new Octokit({ auth: token || undefined });

  try {
    // --- Pass 1: Direct slug probe ---
    // Try the most common slug formats before hitting the search API
    const slugVariants = generateSlugVariants(companyName);

    for (const slug of slugVariants) {
      try {
        const { data } = await octokit.orgs.get({ org: slug });
        if (data.login) {
          console.log(`Resolved "${companyName}" → GitHub org "${data.login}" (exact match)`);
          return data.login;
        }
      } catch {
        // 404 expected for non-matching slugs — continue to next variant
      }
    }

    // --- Pass 2: GitHub search API ---
    const query = `${companyName} in:login type:org`;
    const { data: searchResult } = await octokit.search.users({
      q: query,
      per_page: 5,
    });

    for (const item of searchResult.items) {
      if (item.type === 'Organization') {
        const orgName = item.login.toLowerCase();
        const companySlug = companyName.toLowerCase().replace(/[^a-z0-9]/g, '');

        // Accept if: org contains company slug, company slug contains org, or edit distance ≤ 2
        if (
          orgName.includes(companySlug) ||
          companySlug.includes(orgName) ||
          levenshteinDistance(orgName, companySlug) <= 2
        ) {
          console.log(`Resolved "${companyName}" → GitHub org "${item.login}" (search match)`);
          return item.login;
        }
      }
    }

    console.warn(`Could not resolve GitHub org for company: "${companyName}"`);
    return null;
  } catch (err) {
    console.error(`Error resolving GitHub org for "${companyName}":`, err);
    return null;
  }
}

/**
 * Generates common slug variants of a company name to probe as GitHub org handles.
 *
 * e.g. "Stripe Inc." → ["stripe-inc", "stripeinc", "stripe_inc", "stripe"]
 */
function generateSlugVariants(companyName: string): string[] {
  // Strip common legal suffixes first
  const base = companyName
    .toLowerCase()
    .replace(/\b(inc|corp|ltd|llc|co|company|group|holdings?)\b\.?/g, '')
    .trim();

  const variants = [
    base.replace(/\s+/g, '-'),   // stripe-payments
    base.replace(/\s+/g, ''),    // stripepayments
    base.replace(/\s+/g, '_'),   // stripe_payments
    base.split(/\s+/)[0],        // stripe (first word only — most common)
  ];

  return [...new Set(variants)].filter((v) => v.length >= 2);
}

/**
 * Computes the Levenshtein edit distance between two strings.
 * Used to fuzzy-match search results to the target company name.
 *
 * @returns Number of single-character edits needed to transform `a` into `b`
 */
function levenshteinDistance(a: string, b: string): number {
  const matrix: number[][] = [];

  for (let i = 0; i <= b.length; i++) matrix[i] = [i];
  for (let j = 0; j <= a.length; j++) matrix[0][j] = j;

  for (let i = 1; i <= b.length; i++) {
    for (let j = 1; j <= a.length; j++) {
      if (b.charAt(i - 1) === a.charAt(j - 1)) {
        matrix[i][j] = matrix[i - 1][j - 1];
      } else {
        matrix[i][j] = Math.min(
          matrix[i - 1][j - 1] + 1, // substitution
          matrix[i][j - 1] + 1,     // insertion
          matrix[i - 1][j] + 1      // deletion
        );
      }
    }
  }

  return matrix[b.length][a.length];
}
