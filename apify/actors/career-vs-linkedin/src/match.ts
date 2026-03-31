/**
 * Job title normalization and matching utilities.
 *
 * The goal is to find the same job posting across two platforms (career page and LinkedIn)
 * even when titles differ slightly (e.g. "Sr. Software Engineer" vs "Senior Software Engineer").
 */

const SENIORITY = /\b(sr\.?|jr\.?|senior|junior|lead|principal|staff|associate|mid[-\s]?level|entry[-\s]?level|l[0-9]|i{1,3}|iv|v)\b/gi;
const NOISE_WORDS = /\b(the|a|an|and|or|of|to|in|for|with|on|at|by|from)\b/gi;
const PUNCTUATION = /[^\w\s]/g;
const MULTI_SPACE = /\s+/g;

/** Normalize a job title for fuzzy comparison. */
export function normalizeTitle(title: string): string {
  return title
    .toLowerCase()
    .replace(PUNCTUATION, ' ')
    .replace(SENIORITY, '')
    .replace(NOISE_WORDS, ' ')
    .replace(MULTI_SPACE, ' ')
    .trim();
}

/**
 * Compute Jaccard similarity between two normalized titles (word-set overlap).
 * Returns a value in [0, 1]; 1 = identical word sets.
 */
export function jaccardSimilarity(a: string, b: string): number {
  const setA = new Set(a.split(' ').filter(Boolean));
  const setB = new Set(b.split(' ').filter(Boolean));
  if (setA.size === 0 && setB.size === 0) return 1;
  if (setA.size === 0 || setB.size === 0) return 0;
  let intersection = 0;
  for (const w of setA) { if (setB.has(w)) intersection++; }
  const union = setA.size + setB.size - intersection;
  return intersection / union;
}

/** Returns true if two normalized titles are considered a match (threshold: 0.6). */
export function titlesMatch(normA: string, normB: string, threshold = 0.6): boolean {
  if (normA === normB) return true;
  return jaccardSimilarity(normA, normB) >= threshold;
}

/**
 * Find the best matching title from a candidate list.
 * Returns the candidate and its similarity score, or null if no match exceeds the threshold.
 */
export function findBestMatch(
  query: string,
  candidates: string[],
  threshold = 0.6,
): { match: string; score: number } | null {
  let best: { match: string; score: number } | null = null;
  for (const candidate of candidates) {
    const score = jaccardSimilarity(query, candidate);
    if (score >= threshold && (!best || score > best.score)) {
      best = { match: candidate, score };
    }
  }
  return best;
}

/** Days between two YYYY-MM-DD date strings (positive if dateB > dateA). */
export function daysBetween(dateA: string, dateB: string): number {
  const a = new Date(dateA).getTime();
  const b = new Date(dateB).getTime();
  return Math.round((b - a) / 86_400_000);
}
