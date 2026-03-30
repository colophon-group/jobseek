import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { JobseekClient } from "../client.js";
type HC = { found: boolean; activeListings: number; avgViews: number; avgApplications: number; lowEngagement: boolean; signal: string | null };
type GR = { company: string; overallGhostRisk: number; ghostRate: number; ghostCandidates: number; totalUniqueJobs: number; avgDurationDays: number; recommendation: string; orgGhostSignal: string | null; hiringCafeSignal: HC | null; geminiSummary: string; matchingJobs: Array<{ title: string; durationDays: number; ghostScore: number; ghostReason: string; reposted: boolean }> };

export function register(server: McpServer, client: JobseekClient) {
  server.tool("search_companies", "Search companies by name on jseek.co. Returns up to 10 matching companies with links to their company pages.", { q: z.string().describe("Company name query (min 2 chars)"), locale: z.enum(["en", "de", "fr", "it"]).default("en").describe("Response language") }, { title: "Search Companies", readOnlyHint: true, destructiveHint: false, openWorldHint: true }, async (p) => ({ content: [{ type: "text", text: JSON.stringify(await client.get("/api/v1/companies", { q: p.q, locale: p.locale }), null, 2) }] }));

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

  server.tool("trigger_discovery_run", "Trigger a fresh company-discovery-actor run on Apify. Scans 39+ job boards in parallel batches (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Wellfound, hiring.cafe, Fountain, Rippling, Factorial, Kenjo, LinkedIn, Glassdoor, Softgarden, JOIN, etc.) and updates the dataset. Takes 10–20 min. Call get_discovery_results after completion.", { sources: z.array(z.string()).optional().describe("Override default sources list (omit to use all 39+ sources)"), enableAiDiscovery: z.boolean().optional().describe("Enable Gemini AI portal discovery (default true)") }, { title: "Trigger Discovery Run", readOnlyHint: false, destructiveHint: false, openWorldHint: true }, async (p) => {
    const d = await client.post("/agentic/api/discovery/trigger", p as Record<string, unknown>) as { runId: string; status: string };
    return { content: [{ type: "text", text: `Discovery run started.\nrunId: ${d.runId}\nStatus: ${d.status}\nCall get_discovery_results after ~15 minutes to see updated results.` }] };
  });

  server.tool("get_discovery_results", "Get the latest company discovery results — top hiring companies, fastest-growing (hiring.cafe delta), shrinking companies (possible hiring freeze / layoffs), and source breakdown. Refreshed every ~5 min from 30+ job boards.", {}, { title: "Get Discovery Results", readOnlyHint: true, destructiveHint: false, openWorldHint: true }, async () => {
    type DR = { companiesDiscovered: number; totalJobsTracked?: number; runAt: string; topCompanies: Array<{ name: unknown; jobs: unknown; source: unknown }>; growingCompanies: Array<{ name: unknown; jobs: unknown; delta: unknown }>; shrinkingCompanies?: Array<{ name: unknown; jobs: unknown; delta: unknown }>; newHiringCompanies?: Array<{ name: unknown; jobs: unknown }>; sourceBreakdown?: Record<string, number> };
    const d = await client.get('/agentic/api/discovery', {}) as DR;
    const top = d.topCompanies?.slice(0, 10).map(c => `  ${c.name} — ${c.jobs} jobs (${c.source})`).join('\n') ?? '';
    const growing = d.growingCompanies?.slice(0, 5).map(c => `  ${c.name} +${c.delta} (now ${c.jobs})`).join('\n') ?? '';
    const shrinking = d.shrinkingCompanies?.slice(0, 5).map(c => `  ${c.name} ${c.delta} (now ${c.jobs})`).join('\n') ?? '';
    const newHiring = d.newHiringCompanies?.slice(0, 5).map(c => `  ${c.name} — ${c.jobs} listings (new!)`).join('\n') ?? '';
    const breakdown = d.sourceBreakdown ? Object.entries(d.sourceBreakdown).sort(([,a],[,b])=>b-a).slice(0,10).map(([s,n])=>`  ${s}: ${n}`).join('\n') : '';
    const header = `Discovery as of ${d.runAt?.slice(0, 10)} — ${d.companiesDiscovered} companies${d.totalJobsTracked ? `, ${d.totalJobsTracked.toLocaleString()} total job slots` : ''}`;
    return { content: [{ type: "text", text: [header, top ? `\nTop companies by job count:\n${top}` : '', growing ? `\nFastest-growing (hiring.cafe):\n${growing}` : '', newHiring ? `\nNew burst-hiring companies (first appearance ≥5 jobs):\n${newHiring}` : '', shrinking ? `\nShrinking / hiring freeze signal:\n${shrinking}` : '', breakdown ? `\nTop sources by company count:\n${breakdown}` : ''].filter(Boolean).join('\n') }] };
  });
}
