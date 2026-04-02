export interface CompanyPair {
  name: string;
  careerPageUrl: string;
  /** Indeed company slug, e.g. "Stripe" → indeed.com/cmp/Stripe/jobs */
  indeedSlug?: string;
  /** Glassdoor jobs page URL, e.g. https://www.glassdoor.com/Jobs/OpenAI-Jobs-E2210885.htm */
  glassdoorUrl?: string;
  /** Legacy LinkedIn slug kept for backward compatibility */
  linkedinSlug?: string;
  linkedinCompanyId?: string;
}

export interface Input {
  // ── Single-company mode ────────────────────────────────────────────────────
  companyName?: string;
  careerPageUrl?: string;
  indeedSlug?: string;
  /** @deprecated Use indeedSlug instead */
  linkedinSlug?: string;
  linkedinCompanyId?: string;
  glassdoorUrl?: string;
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
  platform: 'career_page' | 'linkedin' | 'indeed' | 'glassdoor';
  extractionMethod: string;
}

export type Verdict =
  | 'career_first'    // career page appeared N days before job board
  | 'board_first'     // job board appeared N days before career page (anomaly)
  | 'same_day'        // appeared on same day on both platforms
  | 'career_only'     // seen on career page but never indexed by job board
  | 'board_only';     // seen on job board but not found on career page

export interface JobComparison {
  _type: 'job-comparison';
  company: string;
  normalizedTitle: string;
  careerTitle: string;
  boardTitle?: string;
  /** Earliest Wayback snapshot date for this job on the career page. */
  careerPageFirstSeen: string;
  /** datePosted from career page JSON-LD if available (more accurate than firstSeen). */
  careerPageDatePosted?: string;
  /** Earliest Wayback snapshot date for this job on the job board. */
  boardFirstSeen?: string;
  /** datePosted from job board JSON-LD if available. */
  boardDatePosted?: string;
  /** Best available date for career page (datePosted > firstSeen). */
  effectiveCareerDate: string;
  /** Best available date for job board (datePosted > firstSeen). */
  effectiveBoardDate?: string;
  /** Days career page is ahead of job board (positive = career page first). */
  lagDays?: number;
  verdict: Verdict;
  location?: string;
  department?: string;
  careerSnapshotUrl: string;
  boardSnapshotUrl?: string;
}

export interface LagBucket {
  range: string;   // e.g. "1-3 days", "4-7 days"
  count: number;
}

export interface ResearchSummary {
  _type: 'research-summary';
  company: string;
  careerPageUrl: string;
  boardUrl: string;
  boardPlatform: string;   // e.g. "indeed", "linkedin"
  periodStart: string;
  periodEnd: string;
  careerSnapshotsProcessed: number;
  boardSnapshotsProcessed: number;
  totalCareerJobs: number;
  totalBoardJobs: number;
  matchedJobs: number;
  careerOnlyJobs: number;
  boardOnlyJobs: number;
  careerFirstCount: number;
  boardFirstCount: number;
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
