/**
 * @module jobboard-gap-actor/gapDetector
 *
 * Detects hiring gaps: departments where a target company has 0 open roles
 * but its peer companies are actively hiring.
 *
 * Why this matters:
 *   If 5 companies in the same vertical are all hiring Data engineers but
 *   one company (the target) has posted 0 Data jobs, that target is likely
 *   behind on building out that function — which is a high-signal indicator
 *   that they'll need to hire imminently, often through speculative outreach.
 *
 * Signal strength formula:
 *   - Base score: log2(peerAvg + 1) × 2   (how large is the gap?)
 *   - Multiplier: 1 + (peersHiring / totalPeers)  (how consistent across peers?)
 *   - Capped at 10
 *
 * Job title → department classification:
 *   classifyDept() maps job title text to one of 12 department buckets using
 *   keyword lists. Unrecognized titles fall into 'Other'.
 *
 * Used by: jobboard-gap-actor/main.ts
 */

/** Input: a company's open job count broken down by department */
export interface DeptBreakdown {
  company: string;
  /** Key: department name (e.g. "Engineering"), value: number of open roles */
  departments: Record<string, number>;
}

/** A detected gap — one department where target has 0 and peers have >2 on average */
export interface GapResult {
  /** Department name (e.g. "Data", "Engineering") */
  dept: string;
  /** Average openings across peer companies for this department */
  peerAvg: number;
  /** Target company's opening count (always 0 for a gap to be detected) */
  targetCount: number;
  /**
   * Composite signal strength score (0–10).
   * Higher = larger gap AND more peers are hiring in this dept.
   */
  signal_strength: number;
}

/**
 * Detects hiring gaps: departments where the target company has 0 openings
 * but peer companies average more than 2 openings.
 *
 * Returns results sorted by signal_strength descending.
 *
 * @param target - The company being monitored
 * @param peers  - Competitor or comparable companies in the same vertical
 * @returns Array of gap results, highest signal_strength first
 */
export function detectGaps(target: DeptBreakdown, peers: DeptBreakdown[]): GapResult[] {
  if (peers.length === 0) return [];

  // Collect all departments mentioned by any peer
  const allDepts = new Set<string>();
  for (const peer of peers) {
    for (const dept of Object.keys(peer.departments)) {
      allDepts.add(dept);
    }
  }

  const gaps: GapResult[] = [];

  for (const dept of allDepts) {
    const targetCount = target.departments[dept] ?? 0;

    const peerCounts = peers.map((p) => p.departments[dept] ?? 0);
    const peerSum = peerCounts.reduce((sum, count) => sum + count, 0);
    const peerAvg = peerSum / peers.length;

    // Signal only when: target = 0 AND peers average > 2 openings in this dept
    if (targetCount === 0 && peerAvg > 2) {
      const peersHiring = peerCounts.filter((c) => c > 0).length;
      const peerCoverage = peersHiring / peers.length; // Fraction of peers hiring (0–1)

      // Base score grows logarithmically with the size of the gap
      const gapScore = Math.log2(peerAvg + 1) * 2;

      // Multiply by how many peers are hiring — a gap where 5/5 peers hire
      // is stronger than a gap where only 1/5 peers hire
      const coverageMultiplier = 1 + peerCoverage;

      const signal_strength = Math.min(10, parseFloat((gapScore * coverageMultiplier).toFixed(2)));

      gaps.push({
        dept,
        peerAvg: parseFloat(peerAvg.toFixed(1)),
        targetCount,
        signal_strength,
      });
    }
  }

  return gaps.sort((a, b) => b.signal_strength - a.signal_strength);
}

/**
 * Department keyword map.
 * Used by classifyDept() to categorize job titles into one of 12 buckets.
 *
 * To extend: add new keywords to existing arrays or add a new department key.
 * Keywords are matched case-insensitively via String.includes().
 */
export const DEPT_KEYWORDS: Record<string, string[]> = {
  Engineering: [
    'software engineer', 'software developer', 'swe', 'backend', 'frontend',
    'full stack', 'fullstack', 'platform engineer', 'devops', 'sre',
    'infrastructure engineer', 'systems engineer', 'embedded',
  ],
  Data: [
    'data scientist', 'data engineer', 'machine learning', 'ml engineer',
    'ai engineer', 'analytics engineer', 'data analyst', 'bi engineer', 'mlops',
  ],
  Design: [
    'product designer', 'ux designer', 'ui designer', 'ux/ui',
    'visual designer', 'design lead', 'design manager', 'creative director',
  ],
  Product: [
    'product manager', 'product owner', 'pm ', 'head of product',
    'vp product', 'director of product',
  ],
  Sales: [
    'account executive', 'sales development', 'sdr ', 'bdr ',
    'sales manager', 'account manager', 'solutions engineer', 'sales engineer',
  ],
  Marketing: [
    'marketing manager', 'growth marketing', 'content marketing',
    'demand generation', 'seo', 'brand manager', 'marketing director',
  ],
  People: [
    'recruiter', 'talent acquisition', 'hr ', 'people ops',
    'people partner', 'hrbp', 'chief people', 'vp people',
  ],
  Finance: [
    'financial analyst', 'finance manager', 'controller',
    'cfo', 'accounting', 'fp&a', 'treasury',
  ],
  Legal: ['counsel', 'attorney', 'legal', 'compliance', 'paralegal', 'general counsel'],
  Operations: [
    'operations manager', 'operations analyst', 'biz ops',
    'business operations', 'strategy and ops', 'program manager',
  ],
  Security: [
    'security engineer', 'security analyst', 'ciso', 'information security',
    'appsec', 'devsecops', 'penetration',
  ],
  'Customer Success': [
    'customer success', 'customer support', 'technical support',
    'support engineer', 'customer experience',
  ],
};

/**
 * Classifies a job title into a department bucket using keyword matching.
 *
 * @param title - Job title string, e.g. "Senior Data Engineer"
 * @returns Department name (e.g. "Data") or "Other" if no match found
 */
export function classifyDept(title: string): string {
  const lower = title.toLowerCase();
  for (const [dept, keywords] of Object.entries(DEPT_KEYWORDS)) {
    for (const kw of keywords) {
      if (lower.includes(kw)) return dept;
    }
  }
  return 'Other';
}
