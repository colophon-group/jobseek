/**
 * @module twitter-x-actor/scorer
 *
 * Scores tweet text for hiring/growth signal strength using weighted keyword matching.
 *
 * How scoring works:
 *   1. Each keyword in GROWTH_KEYWORDS has a `weight` (1.0–2.5).
 *   2. All matching keyword weights are summed into a rawScore.
 *   3. Bonuses are applied for: multiple matching signals, dollar amounts, role titles.
 *   4. A penalty is applied to retweets (lower signal quality).
 *   5. The raw score is clamped to 0–10.
 *
 * Usage:
 *   const { score, matchedKeywords } = scoreTweet(tweetText);
 *   if (score >= minScore) { // emit as Signal }
 *
 * Downstream:
 *   twitter-x-actor/main.ts filters tweets where score < minScore (default: 5).
 *   The orchestrator re-scores using Claude, so this is a first-pass filter only.
 */

/**
 * Keyword entries with weights.
 * Higher weight = stronger evidence of hiring intent or company growth.
 *
 * Weight guide:
 *   2.5 = near-certain hiring signal ("we're hiring", "just raised")
 *   2.0 = strong growth signal ("building the team", "new office")
 *   1.5 = moderate signal ("scaling", "growing fast")
 *   1.0 = weak/ambient signal ("expanding", "funding")
 */
const GROWTH_KEYWORDS: Array<{ keyword: string; weight: number }> = [
  { keyword: 'scaling', weight: 1.5 },
  { keyword: 'building the team', weight: 2.0 },
  { keyword: "we're hiring", weight: 2.5 },
  { keyword: 'we are hiring', weight: 2.5 },
  { keyword: "we're growing", weight: 1.5 },
  { keyword: 'new office', weight: 2.0 },
  { keyword: 'data center', weight: 1.5 },
  { keyword: 'new region', weight: 2.0 },
  { keyword: 'expanding', weight: 1.0 },
  { keyword: 'just raised', weight: 2.5 },
  { keyword: 'series a', weight: 1.5 },
  { keyword: 'series b', weight: 2.0 },
  { keyword: 'series c', weight: 2.0 },
  { keyword: 'series d', weight: 2.0 },
  { keyword: 'grew our team', weight: 2.0 },
  { keyword: 'doubling down', weight: 1.5 },
  { keyword: 'investing in', weight: 1.0 },
  { keyword: 'infrastructure', weight: 1.0 },
  { keyword: 'platform team', weight: 1.5 },
  { keyword: 'open roles', weight: 2.0 },
  { keyword: 'join us', weight: 1.5 },
  { keyword: 'headcount', weight: 2.0 },
  { keyword: 'funding', weight: 1.5 },
  { keyword: 'raised', weight: 1.0 },
  { keyword: 'hired', weight: 1.0 },
  { keyword: 'onboarding', weight: 1.0 },
  { keyword: 'new hires', weight: 2.0 },
  { keyword: 'growing fast', weight: 1.5 },
  { keyword: 'hypergrowth', weight: 2.5 },
  { keyword: 'unicorn', weight: 1.5 },
];

/** Return type of scoreTweet */
export interface ScoreResult {
  /** Final score, clamped to 0–10 */
  score: number;
  /** List of keywords that matched (useful for signal_text enrichment) */
  matchedKeywords: string[];
}

/**
 * Scores a tweet's text based on growth and hiring keyword density and specificity.
 * Returns a score between 0 and 10.
 *
 * @param text - Raw tweet text (full_text or text field from Twitter API)
 * @returns { score: 0–10, matchedKeywords: string[] }
 */
export function scoreTweet(text: string): ScoreResult {
  if (!text || text.trim().length === 0) {
    return { score: 0, matchedKeywords: [] };
  }

  const lowerText = text.toLowerCase();
  const matchedKeywords: string[] = [];
  let rawScore = 0;

  for (const { keyword, weight } of GROWTH_KEYWORDS) {
    if (lowerText.includes(keyword.toLowerCase())) {
      matchedKeywords.push(keyword);
      rawScore += weight;
    }
  }

  // Bonus: multiple distinct signals in one tweet (compound evidence)
  if (matchedKeywords.length >= 3) {
    rawScore *= 1.2;
  }

  // Bonus: dollar amount mentioned (likely a funding announcement)
  if (/\$\d+(?:\.\d+)?[MBK]?\b/i.test(text)) {
    rawScore += 1.5;
  }

  // Bonus: role title mentioned (tweet is from or about a decision-maker)
  const roleTitles = ['cto', 'vp engineering', 'chief', 'director', 'head of', 'engineer', 'developer'];
  for (const role of roleTitles) {
    if (lowerText.includes(role)) {
      rawScore += 0.5;
      break; // Only apply once regardless of how many roles match
    }
  }

  // Penalty: retweets are typically older news being reshared, lower signal freshness
  if (/^rt\s/i.test(text.trim())) {
    rawScore *= 0.7;
  }

  // Normalize to 0-10
  const normalizedScore = Math.min(10, Math.max(0, rawScore));
  const finalScore = Math.round(normalizedScore * 10) / 10;

  return { score: finalScore, matchedKeywords };
}
