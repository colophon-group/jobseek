# Research method and limits

Date: 28 September 2026. Product snapshot: `be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca` from freshly fetched main, in an isolated worktree on `codex/seo-keyword-research`. User selected a broad English/German/French/Italian market mix.

The design uses 50 deliberately overlapping synthetic personas, each with a separate JSON deliverable. Three persistent subagent workers execute 50 persona assignments in total. The runtime refused a fourth subagent thread even after an assignment completed; this is not a fleet of 50 concurrently or independently instantiated agents. All workers share the same model and product brief. Their agreement is not independent validation, a customer survey, or market-share evidence.

Each persona varies occupation, seniority, urgency, employer awareness, geography, language, and practical constraints. Each assignment produces 12–16 search hypotheses across a journey, runs two representative searches, directly inspects at least one result, records source URLs, identifies objections, and includes poor-fit queries. The assignments include ordinary broad searches to counter the bias caused by knowing Job Seek's feature set in advance. The lead researcher reviews product capabilities, primary competitor documentation, intent/page alignment, and cross-persona patterns, and performs four read-only public API supply spot checks after resolving location slugs.

## What the evidence means

- Every persona query is a hypothesis. `serp-observed` in a raw persona file means the agent executed a query and observed retrieval results; it does not mean another human was observed searching that exact phrase.
- Employer pages establish the wording of an inspected posting, not continued availability, employer-wide policies, Job Seek coverage, or suitability for every applicant.
- Vendor pages establish what a vendor advertises; performance, pricing, or superiority are not independently benchmarked.
- Individual forum posts are anecdotes. Promotional replies and founder posts are not representative demand evidence.
- Web retrieval is not a reproducible localized Google rank audit. Country, device, personalization, and search features are not controlled. Results can contain stale cached content. Current repository facts take precedence for Job Seek product capabilities.
- No Keyword Planner, Search Console, Bing Webmaster Tools, customer interviews, paid keyword database, conversion telemetry, or backlink data was available to this analysis. Search volumes, organic difficulty scores, CPCs, market sizes, and conversion rates are deliberately not invented.
- Repeated themes or counts describe this designed simulation corpus only. Longtail variants and close variants must not be added together as independent demand.

## Product boundaries checked in source

The product supports saved searches, selected companies, email alerts, application stages, interview notes/statistics, and filters including location, occupation, seniority, technology, employment type, work mode, experience, salary, and posting language. Free includes up to ten watchlists; a watchlist is not a one-company limit. Pro Narrowed checks posting text against additional criteria. The current advertised offer is US$10/month after a seven-day trial; it is not an assumed willingness-to-pay estimate.

`toggleSavedJob` requires an indexed posting ID and snapshot. Arbitrary external-job import, browser autofill, automatic application submission, deadline reminders, applicant-side ATS feedback, recruiter/client dashboards, and unlimited commercial reuse of data are not demonstrated. Do not imply these in acquisition pages.

Unknown description details stay unknown: a remote label does not prove worldwide employment, a posting's language does not establish team language, silence about sponsorship/on-call does not establish eligibility/no-on-call, and an 80% workload is not an 80% remote allowance. These are evidence requirements, not legal advice.

The registry contains 6,013 company rows: 1,673 technology, 778 financial services, 504 healthcare, 209 manufacturing, 188 energy, 175 pharma/biotech, 124 transportation/logistics, and 112 robotics, among other sectors. These are configured global companies, not counts of active vacancies or local coverage. They motivate varied research; they do not prove demand.

Source paths: `apps/web/src/components/Hero.tsx`, `Features.tsx`, `Pricing.tsx`, `pro/ProPitch.tsx`; `apps/web/src/lib/search/types.ts`; `apps/web/src/lib/actions/saved-jobs.ts`; `apps/crawler/data/companies.csv`; `industries.csv`; `docs/13-seo-and-indexnow.md`.

## Prioritization

Priorities are editorial judgments, not calculated SEO difficulty or estimated search volume. Compare: fit with demonstrated capabilities; clarity of the searcher's task; evidence that an appropriate result type exists; ability to provide a materially useful answer; and maintenance burden. Keep fit for a free account separate from fit for paid Narrowed. Branded searches are post-awareness checks, not a source of initial nonbrand acquisition.

P0: test first with current product proof. P1: promising, but validate a capability, supply slice, or result format before publishing. P2: conditional expansion requiring more evidence or a different product surface. Exclude: unsupported promise or a different buyer/task.

Do not create one page per persona or keyword permutation. Consolidate synonymous needs into one useful page, and separate genuinely different intents. Preserve Explore's noindex policy; any new indexable vacancy landing surface requires an explicit design and freshness plan.

The public OpenAPI document was also retrieved successfully (HTTP 200 after the /openapi.json redirect). Repository documentation at `packages/mcp-server/README.md` describes the existing hosted MCP endpoint and local package. The developer persona was corrected after this source check; API/MCP acquisition is a separate valid audience, not a missing-feature claim.

Some generated queries contain several scenario constraints and are longer than likely head terms. Preserve them as hypotheses, then test shorter heads and individual modifiers in keyword tools and interviews. Branded queries occur only after hypothetical awareness. Raw persona entry-page suggestions are not endorsements; the synthesis rejects mismatches between vacancy intent and a guide-only page.
