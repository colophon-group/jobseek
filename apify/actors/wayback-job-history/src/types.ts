export interface CompanyInput {
  name: string;
  portalUrl: string;
  inventoryMode?: boolean;   // true = CDX URL inventory mode (better for Workday/SPAs)
}

export interface Input {
  // ── Single-company mode ────────────────────────────────────────────────────
  portalUrl?: string;
  companyName?: string;      // optional human-readable name for reports
  inventoryMode?: boolean;   // true = CDX URL inventory mode (better for Workday/SPAs)
  // ── Batch / discovery mode ─────────────────────────────────────────────────
  batchMode?: boolean;       // true = run on multiple companies with Gemini discovery loop
  companies?: CompanyInput[]; // explicit list; if omitted uses built-in seed list
  discoveryRounds?: number;  // how many Gemini-guided discovery rounds (default 2)
  // ── Shared options ─────────────────────────────────────────────────────────
  startDate?: string;        // YYYY-MM-DD
  endDate?: string;          // YYYY-MM-DD
  maxSnapshots?: number;     // snapshot mode: max daily snapshots (default 100)
  delayMs?: number;          // ms between requests (default 1500)
  googleAiApiKey?: string;   // Gemini key (falls back to env GOOGLE_AI_API_KEY)
}

export interface CdxSnapshot {
  timestamp: string;  // 14-digit YYYYMMDDHHmmss
  original: string;   // original archived URL
}

export interface JobPosting {
  title: string;
  location?: string;
  department?: string;
  url?: string;
  id?: string;
  employmentType?: string;
  validThrough?: string;  // ISO date from schema.org — if in the past, strong ghost signal
}

export interface ExtractionResult {
  jobs: JobPosting[];
  method: string;
}

export interface DayResult {
  date: string;
  timestamp: string;
  snapshotUrl: string;
  jobCount: number;
  jobs: JobPosting[];
  extractionMethod: string;
  error?: string;
}

export interface TimelinePoint {
  date: string;
  jobCount: number;
}

// ── Ghost detection ───────────────────────────────────────────────────────────

export interface JobRecord {
  title: string;
  url: string;
  id?: string;
  location?: string;
  department?: string;
  firstSeen: string;   // YYYY-MM-DD
  lastSeen: string;    // YYYY-MM-DD
  durationDays: number;
  archiveCount: number;
  reposted: boolean;   // true if url disappeared then reappeared
  repostCount: number; // how many times the job disappeared and reappeared
  validThrough?: string; // earliest validThrough date seen in schema.org data
  ghostScore: number;  // 0–100
  ghostReason: string;
}

export interface GhostAnalysis {
  _type: 'ghost-analysis';
  company: string;
  portalUrl: string;
  analysisDate: string;
  periodStart: string;
  periodEnd: string;
  // stats
  totalUniqueJobs: number;
  ghostCandidates: number;       // score >= 70
  ghostRate: number;             // 0–1
  medianDurationDays: number;
  avgDurationDays: number;
  // top offenders
  longestRunningJobs: JobRecord[];
  // org-level signal: null if not triggered
  orgGhostSignal: string | null;
  // Gemini insights
  overallGhostRisk: number;      // 0–100, from Gemini
  topGhostRoles: string[];
  patterns: string[];
  hiringHealthScore: number;     // 0–100 (higher = healthier)
  recommendation: string;        // "Apply confidently" | "Proceed with caution" | "Likely ghost posting"
  geminiSummary: string;
  geminiAvailable: boolean;
}

export interface SummaryRecord {
  _type: 'summary';
  portalUrl: string;
  startDate: string;
  endDate: string;
  totalSnapshotsProcessed: number;
  snapshotsWithJobs: number;
  avgJobCount: number;
  peakDate: string;
  peakJobCount: number;
  latestJobCount: number;
  timeline: TimelinePoint[];
  _company?: string;        // set in batch mode
}

export interface BatchSummaryRecord {
  _type: 'batch-summary';
  round: number;
  companiesAnalyzed: number;
  avgGhostRisk: number;
  worstOffenders: Array<{
    company: string;
    ghostRisk: number;
    recommendation: string;
  }>;
  discoveredCompanies: CompanyInput[];  // companies Gemini suggested for next round
}
