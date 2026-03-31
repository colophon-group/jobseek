/**
 * POST /agentic/api/ghosting/batch
 *
 * Trigger ghost-job analysis for multiple companies in a single request.
 * Launches one Apify actor run per company and returns the array of runIds.
 *
 * Request body:
 *   companies  {Array}  required — 1–10 company entries, each:
 *     portalUrl    {string}  required — career page URL
 *     companyName  {string}  optional — human-readable name
 *     inventoryMode {boolean} optional — CDX inventory mode
 *     maxSnapshots {number}  optional — max daily snapshots (default 100)
 *
 * Response:
 *   { results: [{ companyName, portalUrl, runId, status }] }
 *
 * Poll each runId via GET /agentic/api/ghosting/:runId for results.
 *
 * @example
 * const res = await fetch('/agentic/api/ghosting/batch', {
 *   method: 'POST',
 *   headers: { 'Content-Type': 'application/json' },
 *   body: JSON.stringify({
 *     companies: [
 *       { portalUrl: 'https://boards.greenhouse.io/stripe', companyName: 'Stripe' },
 *       { portalUrl: 'https://jobs.lever.co/rippling', companyName: 'Rippling' },
 *       { portalUrl: 'https://boards.greenhouse.io/openai', companyName: 'OpenAI' },
 *     ]
 *   })
 * });
 * // → { results: [
 * //     { companyName: 'Stripe',   portalUrl: '...', runId: 'abc', status: 'RUNNING' },
 * //     { companyName: 'Rippling', portalUrl: '...', runId: 'def', status: 'RUNNING' },
 * //     { companyName: 'OpenAI',   portalUrl: '...', runId: 'ghi', status: 'RUNNING' },
 * //   ] }
 */
import { NextRequest, NextResponse } from "next/server";
import { verifyGhostingAdminKey, ghostingAdminUnauthorized } from "@/lib/agentic/agentAuth";
import { triggerGhostingRun } from "@/lib/agentic/apify";

const MAX_BATCH = 10;

interface CompanyEntry {
  portalUrl: string;
  companyName?: string;
  inventoryMode?: boolean;
  maxSnapshots?: number;
  delayMs?: number;
}

export async function POST(req: NextRequest) {
  // Batch runs are admin-only — each entry spawns a separate Apify actor
  if (!verifyGhostingAdminKey(req)) return ghostingAdminUnauthorized();

  try {
    const body = await req.json().catch(() => ({}));
    const { companies } = body as { companies?: unknown };

    if (!Array.isArray(companies) || companies.length === 0) {
      return NextResponse.json(
        { error: "companies must be a non-empty array" },
        { status: 400 },
      );
    }

    if (companies.length > MAX_BATCH) {
      return NextResponse.json(
        { error: `Batch size limited to ${MAX_BATCH} companies per request` },
        { status: 400 },
      );
    }

    // Validate each entry
    const entries: CompanyEntry[] = [];
    for (const item of companies) {
      if (!item || typeof item !== "object" || typeof (item as Record<string, unknown>).portalUrl !== "string") {
        return NextResponse.json(
          { error: "Each company entry must have a portalUrl string" },
          { status: 400 },
        );
      }
      entries.push(item as CompanyEntry);
    }

    // Launch all runs in parallel
    const launched = await Promise.all(
      entries.map(async (entry) => {
        try {
          const run = await triggerGhostingRun({
            portalUrl: entry.portalUrl,
            ...(entry.companyName != null && { companyName: entry.companyName }),
            ...(entry.inventoryMode != null && { inventoryMode: entry.inventoryMode }),
            ...(entry.maxSnapshots != null && { maxSnapshots: entry.maxSnapshots }),
            ...(entry.delayMs != null && { delayMs: entry.delayMs }),
          });
          return {
            companyName: entry.companyName ?? entry.portalUrl,
            portalUrl: entry.portalUrl,
            runId: run.id,
            status: run.status,
          };
        } catch (err) {
          return {
            companyName: entry.companyName ?? entry.portalUrl,
            portalUrl: entry.portalUrl,
            runId: null,
            status: "FAILED",
            error: err instanceof Error ? err.message : String(err),
          };
        }
      }),
    );

    return NextResponse.json({ results: launched });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
