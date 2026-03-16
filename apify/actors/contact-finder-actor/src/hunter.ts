/**
 * @module contact-finder-actor/hunter
 *
 * Finds hiring manager contacts using the Hunter.io Domain Search API.
 *
 * API used: GET https://api.hunter.io/v2/domain-search
 * Docs: https://hunter.io/api-documentation/v2#domain-search
 * Pricing: Free tier allows 25 searches/month; paid plans scale up
 *
 * How it works:
 *   1. Searches all known email addresses at the given domain
 *   2. Filters by best match against targetRoles (VP Engineering, CTO, etc.)
 *   3. Weights each candidate by role match score × Hunter's email confidence %
 *   4. Returns the top match as a Contact, or null if no role match found
 *
 * Called by: contact-finder-actor/main.ts (first attempt before Apollo fallback)
 */

import { Contact } from '../../../shared/types';

/** Shape of a single email entry from Hunter.io's domain-search response */
interface HunterEmailEntry {
  value: string;       // Email address
  type: string;        // 'personal' or 'generic'
  confidence: number;  // 0–100: Hunter's confidence the email is valid/active
  first_name?: string;
  last_name?: string;
  position?: string;   // Job title
  seniority?: string;  // e.g. 'senior', 'executive'
  department?: string;
  linkedin?: string;   // LinkedIn profile URL
}

/** Top-level response from Hunter.io's domain-search endpoint */
interface HunterDomainSearchResponse {
  data: {
    domain: string;
    organization?: string;
    emails: HunterEmailEntry[];
  };
  meta: {
    results: number;
    limit: number;
    offset: number;
    params: { domain: string };
  };
}

/**
 * Searches Hunter.io for contacts at a given domain matching target roles.
 * Returns the best-matched contact or null if none found.
 *
 * @param domain      - Company domain to search, e.g. "stripe.com"
 * @param targetRoles - Ordered list of desired titles, e.g. ["VP Engineering", "CTO"]
 * @param apiKey      - Hunter.io API key
 * @param signalId    - The signal ID this contact is being found for (stored in Contact)
 * @returns Best-matched Contact or null
 */
export async function findViaHunter(
  domain: string,
  targetRoles: string[],
  apiKey: string,
  signalId: string
): Promise<Contact | null> {
  if (!apiKey) {
    console.warn('Hunter.io API key not provided, skipping Hunter search');
    return null;
  }

  if (!domain) {
    console.warn('No domain provided for Hunter.io search');
    return null;
  }

  // Fetch up to 20 personal emails — generic/role-based addresses (info@, hr@) are less useful
  const url = `https://api.hunter.io/v2/domain-search?domain=${encodeURIComponent(domain)}&api_key=${apiKey}&limit=20&type=personal`;

  try {
    const response = await fetch(url);

    if (response.status === 429) {
      console.warn('Hunter.io rate limit hit');
      return null;
    }

    if (!response.ok) {
      console.warn(`Hunter.io API error: ${response.status} for domain ${domain}`);
      return null;
    }

    const data = (await response.json()) as HunterDomainSearchResponse;
    const emails = data.data?.emails ?? [];

    if (emails.length === 0) {
      console.log(`No emails found at ${domain} via Hunter.io`);
      return null;
    }

    // Score each email entry: role match × email confidence
    const scored = emails.map((entry) => ({
      entry,
      score: scoreRoleMatch(entry.position ?? '', targetRoles) * (entry.confidence / 100),
    }));

    scored.sort((a, b) => b.score - a.score);
    const best = scored[0];

    if (best.score === 0) {
      console.log(`No role-matching contacts at ${domain} via Hunter.io`);
      return null;
    }

    const entry = best.entry;
    const name = [entry.first_name, entry.last_name].filter(Boolean).join(' ') || 'Unknown';

    const contact: Contact = {
      signal_id: signalId,
      name,
      title: entry.position ?? 'Unknown',
      email: entry.value,
      linkedin_url: entry.linkedin ?? '',
      confidence: entry.confidence / 100, // Normalize to 0–1
    };

    console.log(`Hunter.io found: ${name} (${contact.title}) at ${domain} — confidence: ${entry.confidence}%`);
    return contact;
  } catch (err) {
    console.error(`Hunter.io fetch error for ${domain}:`, err);
    return null;
  }
}

/**
 * Scores how well a job title matches any of the desired target roles.
 *
 * Scoring tiers:
 *   1.0 — Exact case-insensitive match ("VP Engineering" = "VP Engineering")
 *   0.8 — One string contains the other ("Head of Engineering" contains "Engineering")
 *   0.6 × (overlap/max) — Word-level overlap scoring
 *   0.0 — No meaningful overlap
 *
 * @param title       - The candidate's actual job title from Hunter.io
 * @param targetRoles - Desired role titles to match against
 * @returns Score 0–1
 */
function scoreRoleMatch(title: string, targetRoles: string[]): number {
  if (!title) return 0;
  const lower = title.toLowerCase();

  let bestScore = 0;
  for (const role of targetRoles) {
    const roleLower = role.toLowerCase();

    if (lower === roleLower) {
      bestScore = Math.max(bestScore, 1.0);
      continue;
    }

    if (lower.includes(roleLower) || roleLower.includes(lower)) {
      bestScore = Math.max(bestScore, 0.8);
      continue;
    }

    // Word-level overlap: partial credit for shared words
    const titleWords = lower.split(/\s+/);
    const roleWords = roleLower.split(/\s+/);
    const overlap = titleWords.filter((w) => roleWords.includes(w)).length;
    const overlapScore = overlap / Math.max(titleWords.length, roleWords.length);
    bestScore = Math.max(bestScore, overlapScore * 0.6);
  }

  return bestScore;
}
