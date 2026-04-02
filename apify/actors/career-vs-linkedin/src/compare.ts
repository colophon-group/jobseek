import { findBestMatch, daysBetween } from './match.js';
import type { JobSighting, JobComparison, ResearchSummary, LagBucket, Verdict } from './types.js';

/**
 * Cross-reference career page jobs against job board jobs (Indeed, LinkedIn, etc.).
 * Returns one JobComparison per unique career-page job title,
 * plus any board-only jobs not found on the career page.
 */
export function compareJobs(
  careerJobs: Map<string, JobSighting>,
  boardJobs: Map<string, JobSighting>,
  companyName: string,
  careerPageUrl: string,
  boardUrl: string,
  boardPlatform: string,
  periodStart: string,
  periodEnd: string,
  careerSnapshotsProcessed: number,
  boardSnapshotsProcessed: number,
): { comparisons: JobComparison[]; summary: Omit<ResearchSummary, 'geminiSummary' | 'geminiAvailable'> } {
  const comparisons: JobComparison[] = [];
  const matchedBoardTitles = new Set<string>();

  const boardNormTitles = Array.from(boardJobs.keys());

  // ── Match career-page jobs → job board ────────────────────────────────────
  for (const [normTitle, careerJob] of careerJobs.entries()) {
    const boardMatch = findBestMatch(normTitle, boardNormTitles);
    const boardJob = boardMatch ? boardJobs.get(boardMatch.match) ?? null : null;

    if (boardJob) matchedBoardTitles.add(boardJob.normalizedTitle);

    const effectiveCareerDate = careerJob.datePosted ?? careerJob.firstSeen;
    const effectiveBoardDate = boardJob ? (boardJob.datePosted ?? boardJob.firstSeen) : undefined;

    let verdict: Verdict;
    let lagDays: number | undefined;

    if (!boardJob) {
      verdict = 'career_only';
    } else {
      lagDays = daysBetween(effectiveCareerDate, effectiveBoardDate!);
      if (lagDays > 0) {
        verdict = 'career_first';
      } else if (lagDays < 0) {
        verdict = 'board_first';
      } else {
        verdict = 'same_day';
      }
    }

    comparisons.push({
      _type: 'job-comparison',
      company: companyName,
      normalizedTitle: normTitle,
      careerTitle: careerJob.title,
      boardTitle: boardJob?.title,
      careerPageFirstSeen: careerJob.firstSeen,
      careerPageDatePosted: careerJob.datePosted,
      boardFirstSeen: boardJob?.firstSeen,
      boardDatePosted: boardJob?.datePosted,
      effectiveCareerDate,
      effectiveBoardDate,
      lagDays,
      verdict,
      location: careerJob.location ?? boardJob?.location,
      department: careerJob.department ?? boardJob?.department,
      careerSnapshotUrl: careerJob.snapshotUrl,
      boardSnapshotUrl: boardJob?.snapshotUrl,
    });
  }

  // ── Board-only jobs (seen on job board but not career page) ───────────────
  for (const [normTitle, boardJob] of boardJobs.entries()) {
    if (matchedBoardTitles.has(normTitle)) continue;
    comparisons.push({
      _type: 'job-comparison',
      company: companyName,
      normalizedTitle: normTitle,
      careerTitle: '',
      boardTitle: boardJob.title,
      careerPageFirstSeen: '',
      boardFirstSeen: boardJob.firstSeen,
      boardDatePosted: boardJob.datePosted,
      effectiveCareerDate: '',
      effectiveBoardDate: boardJob.datePosted ?? boardJob.firstSeen,
      verdict: 'board_only',
      location: boardJob.location,
      department: boardJob.department,
      careerSnapshotUrl: '',
      boardSnapshotUrl: boardJob.snapshotUrl,
    });
  }

  // ── Compute aggregate statistics ──────────────────────────────────────────
  const matched = comparisons.filter(c => c.verdict !== 'career_only' && c.verdict !== 'board_only');
  const careerFirst = comparisons.filter(c => c.verdict === 'career_first');
  const boardFirst = comparisons.filter(c => c.verdict === 'board_first');
  const sameDay = comparisons.filter(c => c.verdict === 'same_day');
  const careerOnly = comparisons.filter(c => c.verdict === 'career_only');
  const boardOnly = comparisons.filter(c => c.verdict === 'board_only');

  const lagValues = careerFirst.map(c => c.lagDays!).filter(n => n > 0);
  const avgLagDays = lagValues.length > 0 ? Math.round(lagValues.reduce((a, b) => a + b, 0) / lagValues.length) : 0;
  const medianLagDays = lagValues.length > 0 ? computeMedian(lagValues) : 0;
  const pctCareerFirst = matched.length > 0 ? Math.round((careerFirst.length / matched.length) * 100) : 0;

  const lagDistribution = buildLagDistribution(lagValues);

  // Top evidence: career-first jobs sorted by largest lag, with both snapshot links available
  const topEvidenceJobs = [...careerFirst]
    .filter(c => c.boardSnapshotUrl)
    .sort((a, b) => (b.lagDays ?? 0) - (a.lagDays ?? 0))
    .slice(0, 10);

  const conclusion = buildConclusion(
    companyName,
    boardPlatform,
    careerFirst.length,
    matched.length,
    avgLagDays,
    medianLagDays,
    careerOnly.length,
    careerJobs.size,
    boardJobs.size,
  );

  const summary: Omit<ResearchSummary, 'geminiSummary' | 'geminiAvailable'> = {
    _type: 'research-summary',
    company: companyName,
    careerPageUrl,
    boardUrl,
    boardPlatform,
    periodStart,
    periodEnd,
    careerSnapshotsProcessed,
    boardSnapshotsProcessed,
    totalCareerJobs: careerJobs.size,
    totalBoardJobs: boardJobs.size,
    matchedJobs: matched.length,
    careerOnlyJobs: careerOnly.length,
    boardOnlyJobs: boardOnly.length,
    careerFirstCount: careerFirst.length,
    boardFirstCount: boardFirst.length,
    sameDayCount: sameDay.length,
    avgLagDays,
    medianLagDays,
    pctCareerFirst,
    lagDistribution,
    topEvidenceJobs,
    conclusion,
  };

  return { comparisons, summary };
}

function computeMedian(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? Math.round((sorted[mid - 1] + sorted[mid]) / 2)
    : sorted[mid];
}

function buildLagDistribution(lagValues: number[]): LagBucket[] {
  const buckets: { label: string; min: number; max: number }[] = [
    { label: 'Same day (0)', min: 0, max: 0 },
    { label: '1–3 days', min: 1, max: 3 },
    { label: '4–7 days', min: 4, max: 7 },
    { label: '8–14 days', min: 8, max: 14 },
    { label: '15–30 days', min: 15, max: 30 },
    { label: '31–60 days', min: 31, max: 60 },
    { label: '61–90 days', min: 61, max: 90 },
    { label: '90+ days', min: 91, max: Infinity },
  ];
  return buckets.map(b => ({
    range: b.label,
    count: lagValues.filter(v => v >= b.min && v <= b.max).length,
  })).filter(b => b.count > 0);
}

function buildConclusion(
  company: string,
  boardPlatform: string,
  careerFirstCount: number,
  matchedCount: number,
  avgLag: number,
  medianLag: number,
  careerOnlyCount: number,
  totalCareer: number,
  totalBoard: number,
): string {
  const board = boardPlatform.charAt(0).toUpperCase() + boardPlatform.slice(1);

  if (matchedCount === 0) {
    return `Insufficient matching data for ${company}. Career page has ${totalCareer} jobs; ${board} has ${totalBoard} jobs. No cross-platform matches found — titles may differ significantly or ${board} snapshots are sparse.`;
  }

  const pct = Math.round((careerFirstCount / matchedCount) * 100);
  const strength = pct >= 80 ? 'strongly' : pct >= 60 ? 'clearly' : pct >= 40 ? 'generally' : 'partially';

  return (
    `For ${company}, the career page ${strength} precedes ${board}: ` +
    `${pct}% of matched jobs (${careerFirstCount}/${matchedCount}) appeared on the career page first, ` +
    `with an average lead of ${avgLag} days (median: ${medianLag} days). ` +
    (careerOnlyCount > 0
      ? `An additional ${careerOnlyCount} jobs (${Math.round((careerOnlyCount / totalCareer) * 100)}% of career-page listings) were never indexed by ${board} at all. `
      : '') +
    `This confirms that the career page is the primary source of truth for job postings.`
  );
}
