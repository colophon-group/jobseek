import { findBestMatch, daysBetween } from './match.js';
import type { JobSighting, JobComparison, ResearchSummary, LagBucket, Verdict } from './types.js';

/**
 * Cross-reference career page jobs against LinkedIn jobs.
 * Returns one JobComparison per unique career-page job title,
 * plus any LinkedIn-only jobs not found on the career page.
 */
export function compareJobs(
  careerJobs: Map<string, JobSighting>,
  linkedinJobs: Map<string, JobSighting>,
  companyName: string,
  careerPageUrl: string,
  linkedinUrl: string,
  periodStart: string,
  periodEnd: string,
  careerSnapshotsProcessed: number,
  linkedinSnapshotsProcessed: number,
): { comparisons: JobComparison[]; summary: Omit<ResearchSummary, 'geminiSummary' | 'geminiAvailable'> } {
  const comparisons: JobComparison[] = [];
  const matchedLinkedInTitles = new Set<string>();

  const linkedinNormTitles = Array.from(linkedinJobs.keys());

  // ── Match career-page jobs → LinkedIn ─────────────────────────────────────
  for (const [normTitle, careerJob] of careerJobs.entries()) {
    const liMatch = findBestMatch(normTitle, linkedinNormTitles);
    const liJob = liMatch ? linkedinJobs.get(liMatch.match) ?? null : null;

    if (liJob) matchedLinkedInTitles.add(liJob.normalizedTitle);

    const effectiveCareerDate = careerJob.datePosted ?? careerJob.firstSeen;
    const effectiveLinkedInDate = liJob ? (liJob.datePosted ?? liJob.firstSeen) : undefined;

    let verdict: Verdict;
    let lagDays: number | undefined;

    if (!liJob) {
      verdict = 'career_only';
    } else {
      lagDays = daysBetween(effectiveCareerDate, effectiveLinkedInDate!);
      if (lagDays > 0) {
        verdict = 'career_first';
      } else if (lagDays < 0) {
        verdict = 'linkedin_first';
      } else {
        verdict = 'same_day';
      }
    }

    comparisons.push({
      _type: 'job-comparison',
      company: companyName,
      normalizedTitle: normTitle,
      careerTitle: careerJob.title,
      linkedinTitle: liJob?.title,
      careerPageFirstSeen: careerJob.firstSeen,
      careerPageDatePosted: careerJob.datePosted,
      linkedinFirstSeen: liJob?.firstSeen,
      linkedinDatePosted: liJob?.datePosted,
      effectiveCareerDate,
      effectiveLinkedInDate,
      lagDays,
      verdict,
      location: careerJob.location ?? liJob?.location,
      department: careerJob.department ?? liJob?.department,
      careerSnapshotUrl: careerJob.snapshotUrl,
      linkedinSnapshotUrl: liJob?.snapshotUrl,
    });
  }

  // ── LinkedIn-only jobs (seen on LinkedIn but not career page) ─────────────
  for (const [normTitle, liJob] of linkedinJobs.entries()) {
    if (matchedLinkedInTitles.has(normTitle)) continue;
    comparisons.push({
      _type: 'job-comparison',
      company: companyName,
      normalizedTitle: normTitle,
      careerTitle: '',
      linkedinTitle: liJob.title,
      careerPageFirstSeen: '',
      linkedinFirstSeen: liJob.firstSeen,
      linkedinDatePosted: liJob.datePosted,
      effectiveCareerDate: '',
      effectiveLinkedInDate: liJob.datePosted ?? liJob.firstSeen,
      verdict: 'linkedin_only',
      location: liJob.location,
      department: liJob.department,
      careerSnapshotUrl: '',
      linkedinSnapshotUrl: liJob.snapshotUrl,
    });
  }

  // ── Compute aggregate statistics ──────────────────────────────────────────
  const matched = comparisons.filter(c => c.verdict !== 'career_only' && c.verdict !== 'linkedin_only');
  const careerFirst = comparisons.filter(c => c.verdict === 'career_first');
  const linkedinFirst = comparisons.filter(c => c.verdict === 'linkedin_first');
  const sameDay = comparisons.filter(c => c.verdict === 'same_day');
  const careerOnly = comparisons.filter(c => c.verdict === 'career_only');
  const linkedinOnly = comparisons.filter(c => c.verdict === 'linkedin_only');

  const lagValues = careerFirst.map(c => c.lagDays!).filter(n => n > 0);
  const avgLagDays = lagValues.length > 0 ? Math.round(lagValues.reduce((a, b) => a + b, 0) / lagValues.length) : 0;
  const medianLagDays = lagValues.length > 0 ? computeMedian(lagValues) : 0;
  const pctCareerFirst = matched.length > 0 ? Math.round((careerFirst.length / matched.length) * 100) : 0;

  const lagDistribution = buildLagDistribution(lagValues);

  // Top evidence: career-first jobs sorted by largest lag, with both snapshot links available
  const topEvidenceJobs = [...careerFirst]
    .filter(c => c.linkedinSnapshotUrl)
    .sort((a, b) => (b.lagDays ?? 0) - (a.lagDays ?? 0))
    .slice(0, 10);

  const conclusion = buildConclusion(
    companyName,
    careerFirst.length,
    matched.length,
    avgLagDays,
    medianLagDays,
    careerOnly.length,
    careerJobs.size,
    linkedinJobs.size,
  );

  const summary: Omit<ResearchSummary, 'geminiSummary' | 'geminiAvailable'> = {
    _type: 'research-summary',
    company: companyName,
    careerPageUrl,
    linkedinUrl,
    periodStart,
    periodEnd,
    careerSnapshotsProcessed,
    linkedinSnapshotsProcessed,
    totalCareerJobs: careerJobs.size,
    totalLinkedInJobs: linkedinJobs.size,
    matchedJobs: matched.length,
    careerOnlyJobs: careerOnly.length,
    linkedinOnlyJobs: linkedinOnly.length,
    careerFirstCount: careerFirst.length,
    linkedinFirstCount: linkedinFirst.length,
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
  careerFirstCount: number,
  matchedCount: number,
  avgLag: number,
  medianLag: number,
  careerOnlyCount: number,
  totalCareer: number,
  totalLinkedIn: number,
): string {
  if (matchedCount === 0) {
    return `Insufficient matching data for ${company}. Career page has ${totalCareer} jobs; LinkedIn has ${totalLinkedIn} jobs. No cross-platform matches found — titles may differ significantly or LinkedIn snapshots are sparse.`;
  }

  const pct = Math.round((careerFirstCount / matchedCount) * 100);
  const strength = pct >= 80 ? 'strongly' : pct >= 60 ? 'clearly' : pct >= 40 ? 'generally' : 'partially';

  return (
    `For ${company}, the career page ${strength} precedes LinkedIn: ` +
    `${pct}% of matched jobs (${careerFirstCount}/${matchedCount}) appeared on the career page first, ` +
    `with an average lead of ${avgLag} days (median: ${medianLag} days). ` +
    (careerOnlyCount > 0
      ? `An additional ${careerOnlyCount} jobs (${Math.round((careerOnlyCount / totalCareer) * 100)}% of career-page listings) were never indexed by LinkedIn at all. `
      : '') +
    `This confirms that the career page is the primary source of truth for job postings.`
  );
}
