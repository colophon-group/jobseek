import { log } from 'apify';
import type { JobRecord, GhostAnalysis, CompanyInput } from './types.js';

interface GhostStats {
  totalUniqueJobs: number;
  ghostCandidates: number;
  ghostRate: number;
  medianDurationDays: number;
  avgDurationDays: number;
  longestRunningJobs: JobRecord[];
  orgGhostSignal?: string | null;
}

interface GeminiOutput {
  overallGhostRisk: number;
  topGhostRoles: string[];
  patterns: string[];
  hiringHealthScore: number;
  recommendation: string;
  summary: string;
}

/**
 * Run Gemini analysis on ghost job patterns.
 * Returns a partial GhostAnalysis (the AI-generated fields).
 */
export async function analyzeWithGemini(
  apiKey: string, company: string, portalUrl: string, stats: GhostStats, periodStart: string, periodEnd: string,
  hcSignal?: { found: boolean; activeListings: number; avgViews: number; avgApplications: number; lowEngagement: boolean; signal: string | null } | null,
): Promise<Omit<GhostAnalysis, '_type' | 'company' | 'portalUrl' | 'analysisDate' | 'periodStart' | 'periodEnd' | keyof GhostStats> & { geminiAvailable: true }> {

  const { GoogleGenerativeAI } = await import('@google/generative-ai');
  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({ model: 'gemini-2.5-flash' });

  const topJobs = stats.longestRunningJobs.slice(0, 25).map(j => ({
    title: j.title,
    durationDays: j.durationDays,
    ghostScore: j.ghostScore,
    location: j.location ?? 'unknown',
    department: j.department ?? 'unknown',
    reposted: j.reposted,
    repostCount: j.repostCount ?? 0,
    validThrough: j.validThrough ?? null,
    reason: j.ghostReason,
  }));

  const orgSignalLine = stats.orgGhostSignal ? `\n- Org-level signal: ${stats.orgGhostSignal}` : '';
  const hcLine = hcSignal?.found ? `\n- hiring.cafe live signal: ${hcSignal.activeListings} active listings, avg ${hcSignal.avgViews.toFixed(1)} views, ${hcSignal.avgApplications.toFixed(0)} applications${hcSignal.lowEngagement ? ' — LOW ENGAGEMENT (corroborates ghost pattern)' : ' — moderate engagement'}` : hcSignal !== undefined ? '\n- hiring.cafe: company not found (no live listings)' : '';
  const prompt = `You are an expert labor market analyst specializing in "ghost jobs" — job postings companies publish with no genuine intent to fill them in the near term.

Analyze the following historical job posting data for ${company} (${portalUrl}) covering ${periodStart} to ${periodEnd}.

## Stats
- Total unique job URLs tracked: ${stats.totalUniqueJobs}
- Ghost candidates (score ≥ 70): ${stats.ghostCandidates} (${Math.round(stats.ghostRate * 100)}%)
- Median posting duration: ${stats.medianDurationDays} days
- Average posting duration: ${stats.avgDurationDays} days${orgSignalLine}${hcLine}

## Longest-running postings (top 25)
${JSON.stringify(topJobs, null, 2)}

## Your task
Analyze whether this company has a systemic ghost job problem and what patterns you see.

Important nuances to consider:
- "Evergreen" roles (e.g., generic Software Engineer at a large tech company) may stay open intentionally as talent pools — lower ghost risk if the company is actively hiring in general
- Consulting firms (Deloitte, McKinsey, Accenture, Infosys, Wipro) routinely post to build candidate pipelines — higher baseline ghost rate is expected; score accordingly
- A reposted role that clearly changed location or requirements is less suspicious than an identical repost
- hiring.cafe low engagement (if provided) is a strong independent corroborating signal for ghost behavior

Return ONLY valid JSON (no markdown fences):
{
  "overallGhostRisk": <integer 0-100>,
  "topGhostRoles": [<up to 5 role titles that appear to be chronic ghost postings>],
  "patterns": [<3-5 specific patterns you detected, e.g. "Senior manager roles stay open 6+ months", "EMEA roles recycled quarterly">],
  "hiringHealthScore": <integer 0-100, higher = healthier / more trustworthy>,
  "recommendation": <one of: "Apply confidently" | "Proceed with caution" | "Likely ghost posting">,
  "summary": <2-3 sentence plain-English summary a job seeker would find useful>
}`;

  let text = '';
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const result = await model.generateContent(prompt);
      text = result.response.text().trim();
      break;
    } catch (err: unknown) {
      const msg = String(err);
      if (msg.includes('429') || msg.includes('quota')) {
        const wait = 12_000 * (attempt + 1);
        log.warning(`Gemini rate limited, waiting ${wait / 1000}s`);
        await sleep(wait);
      } else {
        log.warning(`Gemini error (attempt ${attempt + 1}): ${err}`);
        await sleep(5_000);
      }
    }
  }

  if (!text) throw new Error('Gemini returned no response after 3 attempts');

  const jsonText = text
    .replace(/^```(?:json)?\n?/, '')
    .replace(/\n?```$/, '')
    .trim();

  const parsed: GeminiOutput = JSON.parse(jsonText);

  return {
    overallGhostRisk: parsed.overallGhostRisk ?? 50,
    topGhostRoles: parsed.topGhostRoles ?? [],
    patterns: parsed.patterns ?? [],
    hiringHealthScore: parsed.hiringHealthScore ?? 50,
    recommendation: parsed.recommendation ?? 'Proceed with caution',
    geminiSummary: parsed.summary ?? '',
    geminiAvailable: true,
  };
}

/**
 * Ask Gemini to suggest more companies worth investigating for ghost jobs,
 * based on the results gathered so far.
 */
export async function discoverGhostCompanies(
  apiKey: string,
  results: GhostAnalysis[],
  round: number,
): Promise<CompanyInput[]> {
  const { GoogleGenerativeAI } = await import('@google/generative-ai');
  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({ model: 'gemini-2.5-flash' });

  const summary = results
    .sort((a, b) => b.overallGhostRisk - a.overallGhostRisk)
    .slice(0, 10)
    .map(r =>
      `- ${r.company}: ghostRisk=${r.overallGhostRisk}/100, ghostRate=${Math.round(r.ghostRate * 100)}%, ` +
      `avgDuration=${r.avgDurationDays}d, recommendation="${r.recommendation}"`,
    )
    .join('\n');

  const alreadyAnalyzed = results.map(r => r.company).join(', ');

  const prompt = `You are a labor market researcher specializing in ghost job postings — roles that companies post indefinitely with no genuine near-term hiring intent.

We have already analyzed these companies (round ${round}):
${summary}

Companies already analyzed (do NOT suggest these): ${alreadyAnalyzed}

Based on this data and your knowledge of companies with historically high ghost job rates, suggest 6 more companies we should investigate next.

High-priority target categories (pick across categories for diversity):
- Indian IT outsourcers with US/EU presence: Infosys, Wipro, TCS, Cognizant, HCL Technologies, Capgemini — notorious for pipeline-building ghost posts
- Big 4 / management consulting: Deloitte, PwC, EY, KPMG, Accenture, McKinsey, BCG, Bain — post evergreen roles year-round
- Defense & government contractors: Leidos, SAIC, Booz Allen Hamilton, Northrop Grumman, Lockheed Martin — slow clearance hiring masks ghost posts
- Banks & fintech: JPMorgan, Goldman Sachs, Citi, Wells Fargo, Deutsche Bank, Credit Suisse (now UBS), Julius Baer — "always interviewing" culture
- Tech giants post-layoff: Meta, Amazon, Microsoft, Google, Salesforce — mass layoffs while maintaining job boards
- Retail / logistics at scale: Walmart, Amazon Logistics, Lidl, Carrefour, Metro AG — high turnover creates perpetual openings
- Swiss/European corporates: Novartis, Roche, Nestlé, ABB, Zurich Insurance, Siemens, Allianz — often post regulatory/compliance roles for long periods

For each company provide the EXACT Wayback-crawlable career portal URL (prefer Workday myworkdayjobs.com URLs, Greenhouse boards.greenhouse.io URLs, or Lever jobs.lever.co URLs where you know them — NOT generic careers pages that redirect to ATS).

Return ONLY valid JSON (no markdown fences), an array of objects:
[
  {
    "name": "Company Name",
    "portalUrl": "https://exact-ats-url.com/board",
    "inventoryMode": true,
    "reason": "one sentence why this company is suspected"
  }
]

inventoryMode should be true for Workday/SPA portals, false for Greenhouse/Lever/Ashby.`;

  let text = '';
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const result = await model.generateContent(prompt);
      text = result.response.text().trim();
      break;
    } catch (err: unknown) {
      const msg = String(err);
      if (msg.includes('429') || msg.includes('quota')) {
        const wait = 12_000 * (attempt + 1);
        log.warning(`Gemini rate limited, waiting ${wait / 1000}s`);
        await sleep(wait);
      } else {
        log.warning(`Gemini discovery error (attempt ${attempt + 1}): ${err}`);
        await sleep(5_000);
      }
    }
  }

  if (!text) {
    log.warning('Gemini discovery returned no response');
    return [];
  }

  const jsonText = text
    .replace(/^```(?:json)?\n?/, '')
    .replace(/\n?```$/, '')
    .trim();

  const parsed: Array<CompanyInput & { reason?: string }> = JSON.parse(jsonText);
  const companies = parsed
    .filter(c => c.name && c.portalUrl)
    .map(({ name, portalUrl, inventoryMode }) => ({ name, portalUrl, inventoryMode: inventoryMode ?? false }));

  log.info(`Gemini suggested ${companies.length} companies for round ${round + 1}`, {
    companies: companies.map(c => c.name),
  });

  return companies;
}

function sleep(ms: number) {
  return new Promise(r => setTimeout(r, ms));
}
