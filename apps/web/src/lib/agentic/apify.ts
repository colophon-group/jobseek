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
