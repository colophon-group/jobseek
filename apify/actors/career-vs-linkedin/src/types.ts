export interface CompanyPair {
  name: string;
  careerPageUrl: string;
  linkedinSlug?: string;       // e.g. "stripe" → linkedin.com/company/stripe/jobs/
  linkedinCompanyId?: string;  // numeric LinkedIn company ID for jobs/search?f_C= URLs
}

export interface Input {
  // ── Single-company mode ────────────────────────────────────────────────────
  companyName?: string;
  careerPageUrl?: string;
  linkedinSlug?: string;
  linkedinCompanyId?: string;
  // ── Batch mode ─────────────────────────────────────────────────────────────
  batchMode?: boolean;
  companies?: CompanyPair[];
  // ── Shared options ─────────────────────────────────────────────────────────
  startDate?: string;      // YYYY-MM-DD, defaults to 1 year ago
  endDate?: string;        // YYYY-MM-DD, defaults to today
  maxSnapshots?: number;   // per-source CDX snapshot limit (default 60)
  delayMs?: number;        // ms between Wayback requests (default 1500)
  googleAiApiKey?: string; // Gemini key (falls back to GOOGLE_AI_API_KEY env var)
}

export interface CdxSnapshot {
  timestamp: string;  // 14-digit YYYYMMDDHHmmss
  original: string;   // original archived URL
}

/** A job posting observed on one specific snapshot date for one platform. */
export interface JobSighting {
  title: string;
  normalizedTitle: string;
  firstSeen: string;         // YYYY-MM-DD of earliest Wayback snapshot where job appears
  datePosted?: string;       // YYYY-MM-DD from JSON-LD datePosted (more accurate than firstSeen)
  snapshotUrl: string;       // Wayback snapshot URL for evidence link
  location?: string;
  department?: string;
  id?: string;               // ATS job ID if available
  platform: 'career_page' | 'linkedin';
  extractionMethod: string;
}

export type Verdict =
  | 'career_first'    // career page appeared N days before LinkedIn
  | 'linkedin_first'  // LinkedIn appeared N days before career page (anomaly)
  | 'same_day'        // appeared on same day on both platforms
  | 'career_only'     // seen on career page but never indexed by LinkedIn
  | 'linkedin_only';  // seen on LinkedIn but not found on career page (syndicated/old)

export interface JobComparison {
  _type: 'job-comparison';
  company: string;
  normalizedTitle: string;
  careerTitle: string;
  linkedinTitle?: string;
  /** Earliest Wayback snapshot date for this job on the career page. */
  careerPageFirstSeen: string;
  /** datePosted from career page JSON-LD if available (more accurate than firstSeen). */
  careerPageDatePosted?: string;
  /** Earliest Wayback snapshot date for this job on LinkedIn. */
  linkedinFirstSeen?: string;
  /** datePosted from LinkedIn JSON-LD if available. */
  linkedinDatePosted?: string;
  /** Best available date for career page (datePosted > firstSeen). */
  effectiveCareerDate: string;
  /** Best available date for LinkedIn (datePosted > firstSeen). */
  effectiveLinkedInDate?: string;
  /** Days career page is ahead of LinkedIn (positive = career page first). */
  lagDays?: number;
  verdict: Verdict;
  location?: string;
  department?: string;
  careerSnapshotUrl: string;
  linkedinSnapshotUrl?: string;
}

export interface LagBucket {
  range: string;   // e.g. "1-3 days", "4-7 days"
  count: number;
}

export interface ResearchSummary {
  _type: 'research-summary';
  company: string;
  careerPageUrl: string;
  linkedinUrl: string;
  periodStart: string;
  periodEnd: string;
  careerSnapshotsProcessed: number;
  linkedinSnapshotsProcessed: number;
  totalCareerJobs: number;
  totalLinkedInJobs: number;
  matchedJobs: number;
  careerOnlyJobs: number;
  linkedinOnlyJobs: number;
  careerFirstCount: number;
  linkedinFirstCount: number;
  sameDayCount: number;
  avgLagDays: number;
  medianLagDays: number;
  /** % of matched jobs where career page was indexed first. */
  pctCareerFirst: number;
  lagDistribution: LagBucket[];
  /** Top 10 jobs with the largest career-page lead as concrete evidence. */
  topEvidenceJobs: JobComparison[];
  conclusion: string;
  geminiSummary?: string;
  geminiAvailable: boolean;
}

export interface BatchSummary {
  _type: 'batch-summary';
  companiesAnalyzed: number;
  avgPctCareerFirst: number;
  avgLagDays: number;
  overallConclusion: string;
  companyResults: {
    company: string;
    pctCareerFirst: number;
    avgLagDays: number;
    matchedJobs: number;
    conclusion: string;
  }[];
}
