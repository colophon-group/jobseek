import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
import { apiLocaleSchema } from "../locale-schema.js";
type HC = { found: boolean; activeListings: number; avgViews: number; avgApplications: number; lowEngagement: boolean; signal: string | null };
type GR = { company: string; overallGhostRisk: number; ghostRate: number; ghostCandidates: number; totalUniqueJobs: number; avgDurationDays: number; recommendation: string; orgGhostSignal: string | null; hiringCafeSignal: HC | null; geminiSummary: string; matchingJobs: Array<{ title: string; durationDays: number; ghostScore: number; ghostReason: string; reposted: boolean }> };

export function register(server: McpServer, client: JobseekClient) {
  server.tool("search_companies", "Search companies by name on jseek.co. Returns up to 10 matching companies with links to their company pages.", { q: z.string().describe("Company name query (min 2 chars)"), locale: apiLocaleSchema }, { title: "Search Companies", readOnlyHint: true, destructiveHint: false, openWorldHint: true }, async (p) => ({ content: [{ type: "text", text: JSON.stringify(await client.get("/api/v1/companies", { q: p.q, locale: p.locale }), null, 2) }] }));

  server.tool("trigger_ghost_analysis", "Start ghost-job analysis for a career page via Wayback Machine. Detects jobs open months without being filled. Returns runId — poll get_ghost_analysis until SUCCEEDED (3–8 min).", { portalUrl: z.string().url().describe("Career page URL e.g. https://boards.greenhouse.io/stripe"), companyName: z.string().optional(), inventoryMode: z.boolean().optional().describe("CDX mode for Workday/SPA portals"), maxSnapshots: z.number().int().min(10).max(500).optional() }, { title: "Trigger Ghost Analysis", readOnlyHint: false, destructiveHint: false, openWorldHint: true }, async (p) => {
    const d = await client.post("/agentic/api/ghosting", p as Record<string, unknown>) as { runId: string; status: string };
    return { content: [{ type: "text", text: `Ghost analysis started.\nrunId: ${d.runId}\nCall get_ghost_analysis every 30s until SUCCEEDED.` }] };
  });

  server.tool("trigger_batch_ghost_analysis", "Analyze multiple companies for ghost jobs in parallel. Up to 10 companies per call. Returns array of runIds — poll each with get_ghost_analysis.", { companies: z.array(z.object({ portalUrl: z.string().url(), companyName: z.string().optional(), inventoryMode: z.boolean().optional(), maxSnapshots: z.number().int().min(10).max(500).optional() })).min(1).max(10).describe("1–10 companies to analyze") }, { title: "Trigger Batch Ghost Analysis", readOnlyHint: false, destructiveHint: false, openWorldHint: true }, async (p) => {
    type BR = { results: Array<{ companyName: string; portalUrl: string; runId: string | null; status: string; error?: string }> };
    const d = await client.post("/agentic/api/ghosting/batch", { companies: p.companies }) as BR;
    const lines = d.results.map(r => r.runId ? `  ${r.companyName}: runId=${r.runId} (${r.status})` : `  ${r.companyName}: FAILED — ${r.error ?? 'unknown'}`);
    return { content: [{ type: "text", text: [`Batch ghost analysis started for ${d.results.length} companies:`, ...lines, '\nPoll each runId with get_ghost_analysis every 30s.'].join('\n') }] };
  });

  server.tool("get_ghost_analysis", "Poll ghost-job results. Returns overallGhostRisk (0–100), ghostRate, orgGhostSignal, hiringCafeSignal (live engagement), geminiSummary, and top ghost roles.", { runId: z.string().describe("runId from trigger_ghost_analysis"), position: z.string().optional().describe("Filter jobs by position title") }, { title: "Get Ghost Analysis", readOnlyHint: true, destructiveHint: false, openWorldHint: true }, async (p) => {
    const d = await client.get(`/agentic/api/ghosting/${p.runId}`, p.position ? { position: p.position } : {}) as { status: string; result: GR | null };
    if (!d.result) return { content: [{ type: "text", text: `Status: ${d.status} — still running, retry in 30s.` }] };
    const r = d.result; const hc = r.hiringCafeSignal;
    const hcLine = hc ? (hc.found ? `hiring.cafe: ${hc.activeListings} listings, avg ${hc.avgViews.toFixed(1)} views, ${hc.avgApplications.toFixed(0)} apps${hc.lowEngagement ? " — LOW ENGAGEMENT (ghost signal)" : ""}` : "hiring.cafe: not found") : "";
    const jobs = r.matchingJobs.sort((a, b) => b.ghostScore - a.ghostScore).slice(0, 8).map(j => `  [${j.ghostScore}/100] ${j.title} — ${j.durationDays}d${j.reposted ? " (reposted)" : ""}: ${j.ghostReason}`);
    return { content: [{ type: "text", text: [`=== Ghost Analysis: ${r.company} ===`, `Risk: ${r.overallGhostRisk}/100 | Rate: ${Math.round(r.ghostRate * 100)}% (${r.ghostCandidates}/${r.totalUniqueJobs}) | Avg ${r.avgDurationDays} days`, `Recommendation: ${r.recommendation}`, r.orgGhostSignal ?? "", hcLine, r.geminiSummary, jobs.length ? `\nTop ghost roles:\n${jobs.join("\n")}` : ""].filter(Boolean).join("\n") }] };
  });

}
