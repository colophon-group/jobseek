import { ApifyClient } from "apify-client";

function getClient(): ApifyClient {
  const token = process.env.APIFY_TOKEN;
  if (!token) throw new Error("APIFY_TOKEN is not set");
  return new ApifyClient({ token });
}

export const OUTREACH_DATASET_NAME = "outreach-ready";

export interface ApifyOutreachRecord {
  signal_id: string;
  signal_company: string;
  signal_type: string;
  signal_text: string;
  signal_date: string;
  final_score: number;
  scoring_reasoning: string;
  contact: {
    signal_id: string;
    name: string;
    title: string;
    email: string;
    linkedin_url: string;
    confidence: number;
  };
  subject: string;
  body: string;
  status: string;
  created_at: string;
  // source url from the original signal
  source_url?: string;
  // careers page url
  careers_url?: string;
}

export async function fetchOutreachDataset(
  datasetNameOrId?: string,
): Promise<ApifyOutreachRecord[]> {
  const client = getClient();
  const name = datasetNameOrId ?? OUTREACH_DATASET_NAME;
  const dataset = await client.dataset(name).listItems({ clean: true });
  return dataset.items as unknown as ApifyOutreachRecord[];
}

export async function triggerOrchestratorRun(
  input: Record<string, unknown>,
): Promise<{ id: string; status: string }> {
  const client = getClient();
  const actorId = process.env.APIFY_ORCHESTRATOR_ACTOR_ID;
  if (!actorId) throw new Error("APIFY_ORCHESTRATOR_ACTOR_ID is not set");
  const run = await client.actor(actorId).call(input, { waitSecs: 0 });
  return { id: run.id, status: run.status };
}

export async function getRunStatus(
  runId: string,
): Promise<{ id: string; status: string; finishedAt: string | null }> {
  const client = getClient();
  const run = await client.run(runId).get();
  if (!run) throw new Error(`Run ${runId} not found`);
  return {
    id: run.id,
    status: run.status,
    finishedAt: run.finishedAt ? run.finishedAt.toISOString() : null,
  };
}

// ── Ghosting research (wayback-job-history actor) ─────────────────────────────

export interface GhostingRunInput {
  portalUrl: string;
  companyName?: string;
  inventoryMode?: boolean;
  maxSnapshots?: number;
  delayMs?: number;
}

export async function triggerGhostingRun(
  input: GhostingRunInput,
): Promise<{ id: string; status: string }> {
  const client = getClient();
  const actorId = process.env.APIFY_GHOSTING_ACTOR_ID;
  if (!actorId) throw new Error("APIFY_GHOSTING_ACTOR_ID is not set");
  const run = await client.actor(actorId).call(input, { waitSecs: 0 });
  return { id: run.id, status: run.status };
}

export async function triggerDiscoveryRun(
  input: Record<string, unknown> = {},
): Promise<{ id: string; status: string }> {
  const client = getClient();
  const actorId = process.env.APIFY_DISCOVERY_ACTOR_ID;
  if (!actorId) throw new Error("APIFY_DISCOVERY_ACTOR_ID is not set");
  const run = await client.actor(actorId).call(input, { waitSecs: 0 });
  return { id: run.id, status: run.status };
}

export type GhostingRunResult =
  | { runId: string; status: string; finishedAt: null; result: null }
  | { runId: string; status: string; finishedAt: string; result: GhostAnalysisResult };

export interface HiringCafeSignal {
  found: boolean;
  activeListings: number;
  avgViews: number;
  avgApplications: number;
  lowEngagement: boolean;
  signal: string | null;
}

export interface GhostAnalysisResult {
  company: string;
  portalUrl: string;
  analysisDate: string;
  periodStart: string;
  periodEnd: string;
  totalUniqueJobs: number;
  ghostCandidates: number;
  ghostRate: number;
  medianDurationDays: number;
  avgDurationDays: number;
  overallGhostRisk: number;
  hiringHealthScore: number;
  recommendation: string;
  topGhostRoles: string[];
  patterns: string[];
  geminiSummary: string;
  geminiAvailable: boolean;
  orgGhostSignal: string | null;
  hiringCafeSignal: HiringCafeSignal | null;
  matchingJobs: GhostJobRecord[];
}

export interface GhostJobRecord {
  title: string;
  url: string;
  id?: string;
  location?: string;
  department?: string;
  firstSeen: string;
  lastSeen: string;
  durationDays: number;
  archiveCount: number;
  reposted: boolean;
  repostCount?: number;
  validThrough?: string;
  ghostScore: number;
  ghostReason: string;
}

export async function getGhostingResult(
  runId: string,
  positionFilter?: string,
): Promise<GhostingRunResult> {
  const client = getClient();
  const run = await client.run(runId).get();
  if (!run) throw new Error(`Run ${runId} not found`);

  const finishedAt = run.finishedAt ? run.finishedAt.toISOString() : null;

  if (run.status !== "SUCCEEDED" || !finishedAt) {
    return { runId: run.id, status: run.status, finishedAt: null, result: null };
  }

  const dataset = await client
    .dataset(run.defaultDatasetId)
    .listItems({ clean: true });

  const items = dataset.items as Record<string, unknown>[];

  const analysis = items.find(
    (i) => i._type === "ghost-analysis",
  ) as Record<string, unknown> | undefined;

  const jobRecords = items.filter(
    (i) => i._type === "job-record",
  ) as unknown as GhostJobRecord[];

  // Fall back to longestRunningJobs from the analysis record if no separate job-record items exist
  // (can happen when all jobs scored below the 40-point push threshold in snapshot mode)
  const effectiveJobs: GhostJobRecord[] = jobRecords.length > 0
    ? jobRecords
    : ((analysis?.longestRunningJobs as GhostJobRecord[] | undefined) ?? []);

  const matchingJobs = positionFilter
    ? effectiveJobs.filter((j) =>
        j.title?.toLowerCase().includes(positionFilter.toLowerCase()),
      )
    : effectiveJobs;

  if (!analysis) {
    // Run succeeded but no analysis record yet — treat as still running
    return { runId: run.id, status: "RUNNING", finishedAt: null, result: null };
  }

  return {
    runId: run.id,
    status: run.status,
    finishedAt,
    result: {
      company: analysis.company as string,
      portalUrl: analysis.portalUrl as string,
      analysisDate: analysis.analysisDate as string,
      periodStart: analysis.periodStart as string,
      periodEnd: analysis.periodEnd as string,
      totalUniqueJobs: analysis.totalUniqueJobs as number,
      ghostCandidates: analysis.ghostCandidates as number,
      ghostRate: analysis.ghostRate as number,
      medianDurationDays: analysis.medianDurationDays as number,
      avgDurationDays: analysis.avgDurationDays as number,
      overallGhostRisk: analysis.overallGhostRisk as number,
      hiringHealthScore: analysis.hiringHealthScore as number,
      recommendation: analysis.recommendation as string,
      topGhostRoles: (analysis.topGhostRoles as string[]) ?? [],
      patterns: (analysis.patterns as string[]) ?? [],
      geminiSummary: analysis.geminiSummary as string,
      geminiAvailable: analysis.geminiAvailable as boolean,
      orgGhostSignal: (analysis.orgGhostSignal as string | null) ?? null,
      hiringCafeSignal: (analysis.hiringCafeSignal as HiringCafeSignal | null) ?? null,
      matchingJobs: matchingJobs.sort((a, b) => b.ghostScore - a.ghostScore),
    },
  };
}
