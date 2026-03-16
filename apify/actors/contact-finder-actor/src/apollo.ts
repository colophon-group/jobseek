/**
 * @module contact-finder-actor/apollo
 *
 * Finds hiring manager contacts using the Apollo.io People Search API.
 * Used as a fallback when Hunter.io doesn't find a role-matched contact.
 *
 * API used: POST https://api.apollo.io/v1/mixed_people/search
 * Docs: https://developer.apollo.io/reference/people_search
 * Auth: X-Api-Key header
 *
 * How it works:
 *   1. Searches Apollo's contact database by company name + target role titles
 *   2. Filters for verified or likely-to-engage email status
 *   3. Scores each result by role match + seniority
 *   4. Returns the top match as a Contact, or null if no role match found
 *
 * Confidence calculation:
 *   - 0.95 × roleMatchScore if Apollo's email_status = 'verified'
 *   - 0.70 × roleMatchScore otherwise
 *
 * Called by: contact-finder-actor/main.ts (second attempt, after Hunter.io)
 */

import { Contact } from '../../../shared/types';

/** Shape of a single person result from Apollo.io's people search */
interface ApolloPerson {
  id: string;
  first_name?: string;
  last_name?: string;
  name?: string;
  title?: string;
  email?: string;
  /** 'verified' | 'likely to engage' | 'unavailable' | etc. */
  email_status?: string;
  linkedin_url?: string;
  organization?: {
    name?: string;
    website_url?: string;
  };
}

/** Top-level response from Apollo.io's people search endpoint */
interface ApolloSearchResponse {
  people: ApolloPerson[];
  pagination: {
    page: number;
    per_page: number;
    total_entries: number;
    total_pages: number;
  };
}

/**
 * Searches Apollo.io for contacts matching target roles at a given company.
 * Returns the best-matched contact or null if none found.
 *
 * @param company     - Company name to search, e.g. "Stripe" (not a domain)
 * @param targetRoles - Ordered list of desired titles, e.g. ["VP Engineering", "CTO"]
 * @param apiKey      - Apollo.io API key
 * @param signalId    - The signal ID this contact is being found for (stored in Contact)
 * @returns Best-matched Contact or null
 */
export async function findViaApollo(
  company: string,
  targetRoles: string[],
  apiKey: string,
  signalId: string
): Promise<Contact | null> {
  if (!apiKey) {
    console.warn('Apollo.io API key not provided, skipping Apollo search');
    return null;
  }

  if (!company) {
    console.warn('No company provided for Apollo.io search');
    return null;
  }

  // Extract individual words from role titles for Apollo's title keyword search
  const titleKeywords = targetRoles.flatMap((role) =>
    role.split(/\s+/).filter((w) => w.length > 2)
  );

  const requestBody = {
    q_organization_name: company,
    q_titles: targetRoles,            // Apollo filters to people with these titles
    per_page: 10,
    page: 1,
    contact_email_status: ['verified', 'likely to engage'], // Exclude bounced/invalid
  };

  try {
    const response = await fetch('https://api.apollo.io/v1/mixed_people/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
        'X-Api-Key': apiKey,
      },
      body: JSON.stringify(requestBody),
    });

    if (response.status === 429) {
      console.warn('Apollo.io rate limit hit');
      return null;
    }

    if (!response.ok) {
      const errorText = await response.text();
      console.warn(`Apollo.io API error ${response.status}: ${errorText.slice(0, 200)}`);
      return null;
    }

    const data = (await response.json()) as ApolloSearchResponse;
    const people = data.people ?? [];

    if (people.length === 0) {
      console.log(`No contacts found for "${company}" at Apollo.io`);
      return null;
    }

    // Score each person by role match quality
    const scored = people.map((person) => ({
      person,
      score: scorePersonMatch(person, targetRoles, titleKeywords),
    }));

    scored.sort((a, b) => b.score - a.score);
    const best = scored[0];

    if (best.score === 0) {
      console.log(`No role-matching contacts found for "${company}" at Apollo.io`);
      return null;
    }

    const person = best.person;
    const name = person.name ?? [person.first_name, person.last_name].filter(Boolean).join(' ') ?? 'Unknown';
    const email = person.email ?? '';

    // Confidence combines role match quality with email deliverability certainty
    const emailConfidence = person.email_status === 'verified' ? 0.95 : 0.7;
    const confidence = Math.min(0.99, best.score * emailConfidence);

    const contact: Contact = {
      signal_id: signalId,
      name,
      title: person.title ?? 'Unknown',
      email,
      linkedin_url: person.linkedin_url ?? '',
      confidence: parseFloat(confidence.toFixed(2)),
    };

    console.log(`Apollo.io found: ${name} (${contact.title}) at ${company} — confidence: ${Math.round(confidence * 100)}%`);
    return contact;
  } catch (err) {
    console.error(`Apollo.io fetch error for company "${company}":`, err);
    return null;
  }
}

/**
 * Scores how well a person matches the desired roles, with a seniority bonus.
 *
 * Scoring tiers:
 *   1.0  — Exact title match
 *   0.85 — Partial match (one contains the other)
 *   0.6× — Keyword overlap score
 *   +0.2 — Bonus for senior titles (VP, Chief, Head, Director, C-suite)
 *
 * @param person        - Apollo person object
 * @param targetRoles   - Desired role titles
 * @param titleKeywords - Individual words extracted from targetRoles
 * @returns Score 0–1
 */
function scorePersonMatch(
  person: ApolloPerson,
  targetRoles: string[],
  titleKeywords: string[]
): number {
  const title = (person.title ?? '').toLowerCase();
  if (!title) return 0;

  let score = 0;

  for (const role of targetRoles) {
    const roleLower = role.toLowerCase();
    if (title === roleLower) {
      score = Math.max(score, 1.0);
    } else if (title.includes(roleLower) || roleLower.includes(title)) {
      score = Math.max(score, 0.85);
    }
  }

  if (score > 0) return score;

  // Fall back to keyword overlap if no title match
  const titleWords = title.split(/\s+/);
  const matchedKeywords = titleKeywords.filter((kw) => titleWords.includes(kw.toLowerCase()));
  score = matchedKeywords.length / Math.max(titleKeywords.length, 1) * 0.6;

  // Seniority bonus: these titles are more likely to be decision-makers
  const seniorityBoost = ['vp', 'chief', 'head', 'director', 'cto', 'coo', 'ceo'].some((s) =>
    title.includes(s)
  );
  if (seniorityBoost) score += 0.2;

  return Math.min(1, score);
}
