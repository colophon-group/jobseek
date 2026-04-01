import { log } from 'apify';
import type { ResearchSummary, JobComparison } from './types.js';

/**
 * Use Gemini to generate a research-quality narrative summary of the career-page-vs-LinkedIn
 * timing analysis. Returns the geminiSummary string to embed in the ResearchSummary record.
 */
export async function analyzeWithGemini(
  apiKey: string,
  summary: Omit<ResearchSummary, 'geminiSummary' | 'geminiAvailable'>,
  topEvidence: JobComparison[],
): Promise<string> {
  const { GoogleGenerativeAI } = await import('@google/generative-ai');
  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({ model: 'gemini-2.5-flash' });

  const board = summary.boardPlatform.charAt(0).toUpperCase() + summary.boardPlatform.slice(1);

  const evidenceLines = topEvidence.slice(0, 8).map(j =>
    `  • "${j.careerTitle}" — career page: ${j.effectiveCareerDate}, ${board}: ${j.effectiveBoardDate ?? 'not found'}, lag: ${j.lagDays != null ? `+${j.lagDays} days` : 'career only'}`
  ).join('\n');

  const prompt = `You are a labor market researcher analyzing whether company career pages publish job postings before ${board}.

## Data for ${summary.company}
- Analysis period: ${summary.periodStart} to ${summary.periodEnd}
- Career page snapshots processed: ${summary.careerSnapshotsProcessed}
- ${board} snapshots processed: ${summary.boardSnapshotsProcessed}
- Career page unique jobs: ${summary.totalCareerJobs}
- ${board} unique jobs: ${summary.totalBoardJobs}
- Jobs matched across both platforms: ${summary.matchedJobs}
- Career page first: ${summary.careerFirstCount} (${summary.pctCareerFirst}%)
- ${board} first: ${summary.boardFirstCount}
- Same day: ${summary.sameDayCount}
- Career page only (never on ${board}): ${summary.careerOnlyJobs}
- Average lag (career ahead of ${board}): ${summary.avgLagDays} days
- Median lag: ${summary.medianLagDays} days

## Top evidence jobs (career page appeared first)
${evidenceLines || `  (no matched jobs with ${board} snapshots available)`}

## Lag distribution
${summary.lagDistribution.map(b => `  ${b.range}: ${b.count} jobs`).join('\n')}

Write a concise, research-quality narrative (3–5 paragraphs) that:
1. States the key finding clearly (does career page precede ${board}, and by how much?)
2. Interprets the lag distribution — what does it mean for job seekers?
3. Notes any caveats (data limitations, ${board} crawl coverage, CDX snapshot frequency)
4. Gives a practical recommendation for job seekers (e.g. "Monitor career pages directly to see jobs 5–14 days before they reach ${board}")

Be specific with numbers. Do NOT use bullet points — write flowing prose.`;

  const result = await model.generateContent(prompt);
  const text = result.response.text().trim();
  log.info(`Gemini analysis complete for ${summary.company}`, { chars: text.length });
  return text;
}
