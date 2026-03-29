export default function Page() {
  const base = "https://jobseek.colophon-group.org/agentic/api";

  const authEndpoints = [
    {
      method: "GET",
      path: "/api/me",
      summary: "Verify your API key and check subscription status",
      auth: true,
      params: [],
      returns: "{ userId, email, name, subscription: { plan, status, endsAt } | null }",
      example: `GET ${base}/me\nAuthorization: Bearer <userId>`,
    },
    {
      method: "GET",
      path: "/api/checkout",
      summary: "Create a Stripe checkout session for the user",
      auth: false,
      params: [
        { name: "userId", type: "string", required: true, desc: "The user's ID (UUID)" },
        { name: "successUrl", type: "string", required: false, desc: "Redirect URL after successful payment" },
        { name: "cancelUrl", type: "string", required: false, desc: "Redirect URL on cancellation" },
      ],
      returns: "{ checkoutUrl: string } — open this URL in the user's browser to complete payment",
      example: `GET ${base}/checkout?userId=<userId>`,
    },
  ];

  const ghostingEndpoints = [
    {
      method: "POST",
      path: "/api/ghosting",
      summary: "Trigger a ghost-job analysis for a company career portal",
      auth: false,
      params: [
        { name: "portalUrl", type: "string", required: true, desc: "Career page URL (e.g. https://boards.greenhouse.io/stripe)" },
        { name: "companyName", type: "string", required: false, desc: "Human-readable name for reports. Auto-detected from URL if omitted." },
        { name: "inventoryMode", type: "boolean", required: false, desc: "CDX inventory mode for Workday/SPA portals (default false)" },
        { name: "maxSnapshots", type: "number", required: false, desc: "Max daily snapshots to process (default 100)" },
        { name: "delayMs", type: "number", required: false, desc: "Delay between Wayback requests in ms (default 1500)" },
      ],
      returns: "{ runId: string, status: string } — poll GET /api/ghosting/:runId for results",
      example: `POST ${base}/ghosting\nContent-Type: application/json\n\n{ "portalUrl": "https://boards.greenhouse.io/stripe", "companyName": "Stripe" }`,
    },
    {
      method: "GET",
      path: "/api/ghosting/[runId]",
      summary: "Poll the status and result of a ghost-job analysis run",
      auth: false,
      params: [
        { name: "position", type: "string", required: false, desc: "Filter matchingJobs to titles containing this string (query param)" },
      ],
      returns: `{ runId, status, finishedAt, result: { company, portalUrl, totalUniqueJobs, ghostCandidates, ghostRate, overallGhostRisk, hiringHealthScore, recommendation, topGhostRoles, patterns, geminiSummary, orgGhostSignal, matchingJobs[] } | null }`,
      example: `GET ${base}/ghosting/<runId>?position=Staff+Engineer`,
    },
    {
      method: "POST",
      path: "/api/ghosting/paid",
      summary: "Same as POST /api/ghosting but requires an active subscription",
      auth: true,
      params: [
        { name: "portalUrl", type: "string", required: true, desc: "Career page URL" },
        { name: "companyName", type: "string", required: false, desc: "Human-readable company name" },
        { name: "maxSnapshots", type: "number", required: false, desc: "Max snapshots (default 100)" },
      ],
      returns: "{ runId: string, status: string }",
      example: `POST ${base}/ghosting/paid\nAuthorization: Bearer <userId>\nContent-Type: application/json\n\n{ "portalUrl": "https://jobs.lever.co/rippling" }`,
    },
  ];

  const utilityEndpoints = [
    {
      method: "GET",
      path: "/api/discovery",
      summary: "Show the live portal registry from the latest company-discovery run",
      auth: false,
      params: [],
      returns: `{ runId, runAt, runStatus, companiesDiscovered, registry: { portals[] }, topCompanies: [{ name, jobs, source, url }], growingCompanies: [{ name, jobs, prevJobs, delta, url }] }`,
      example: `GET ${base}/discovery`,
    },
    {
      method: "GET",
      path: "/api/ping",
      summary: "Health check — returns { ok: true }",
      auth: false,
      params: [],
      returns: "{ ok: true }",
      example: `GET ${base}/ping`,
    },
  ];

  const dataEndpoints = [
    {
      method: "GET",
      path: "/api/jobs",
      summary: "Search job postings",
      params: [
        { name: "q", type: "string", required: false, desc: "Full-text search query" },
        { name: "loc", type: "string", required: false, desc: "Location filter (city, country, or 'remote')" },
        { name: "occ", type: "string", required: false, desc: "Occupation slug (from /api/taxonomies?type=occupations)" },
        { name: "sen", type: "string", required: false, desc: "Seniority slug (from /api/taxonomies?type=seniority)" },
        { name: "tech", type: "string", required: false, desc: "Technology slug (from /api/taxonomies?type=technologies)" },
        { name: "sal", type: "string", required: false, desc: "Minimum annual salary in USD" },
        { name: "exp", type: "string", required: false, desc: "Maximum years of experience required" },
        { name: "locale", type: "string", required: false, desc: "Response language: en (default), de, fr, it" },
      ],
      returns: "{ companies: Company[], totalCompanies: number, moreAt: string | null }",
      example: `GET ${base}/jobs?q=typescript&sen=senior\nAuthorization: Bearer <userId>`,
    },
    {
      method: "GET",
      path: "/api/jobs/[id]",
      summary: "Get a single job posting by ID",
      params: [
        { name: "id", type: "string", required: true, desc: "Job posting ID (from search results)" },
        { name: "locale", type: "string", required: false, desc: "Response language: en (default), de, fr, it" },
      ],
      returns: "{ id, title, company, locations, seniority, technologies, salary, experience, url, postedAt, ... }",
      example: `GET ${base}/jobs/abc123\nAuthorization: Bearer <userId>`,
    },
    {
      method: "GET",
      path: "/api/companies",
      summary: "Search companies by name",
      params: [
        { name: "q", type: "string", required: false, desc: "Company name search query" },
        { name: "locale", type: "string", required: false, desc: "Response language: en (default), de, fr, it" },
      ],
      returns: "{ slug, name, website, logo }[]",
      example: `GET ${base}/companies?q=stripe\nAuthorization: Bearer <userId>`,
    },
    {
      method: "GET",
      path: "/api/taxonomies",
      summary: "List valid filter values",
      params: [
        { name: "type", type: "enum", required: true, desc: "seniority | occupations | technologies | industries" },
        { name: "locale", type: "string", required: false, desc: "Response language: en (default), de, fr, it" },
      ],
      returns: "{ slug, label }[]",
      example: `GET ${base}/taxonomies?type=seniority\nAuthorization: Bearer <userId>`,
    },
  ];

  return (
    <div className="space-y-10">
      {/* Title */}
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Agentic API</h1>
        <p className="text-muted text-sm max-w-2xl">
          REST API for AI agents to search the Job Seek job index. Requires an active subscription.
          All responses are JSON.
        </p>
        <div className="flex items-center gap-2 pt-1">
          <span className="font-mono text-xs bg-surface border border-divider rounded px-2 py-0.5 text-muted">
            Base URL
          </span>
          <code className="font-mono text-xs text-foreground">{base}</code>
        </div>
      </div>

      {/* Auth model */}
      <section className="space-y-3">
        <h2 className="text-base font-semibold tracking-tight">Authentication &amp; Paywall</h2>
        <p className="text-sm text-muted max-w-2xl">
          All data endpoints require an{" "}
          <code className="font-mono text-xs">Authorization: Bearer &lt;userId&gt;</code> header,
          where <code className="font-mono text-xs">userId</code> is the UUID from the user&apos;s
          Job Seek account. The user must have an active paid subscription.
        </p>

        <div className="grid gap-3 sm:grid-cols-3">
          {[
            {
              status: "401",
              label: "Unauthorized",
              color: "text-yellow-500",
              desc: "Token missing or user ID not found. Prompt the user to sign in at jobseek.colophon-group.org and share their user ID.",
            },
            {
              status: "402",
              label: "Payment Required",
              color: "text-orange-500",
              desc: "User exists but has no active subscription. Use GET /api/checkout to get a Stripe URL and open it for the user.",
            },
            {
              status: "200",
              label: "OK",
              color: "text-green-500",
              desc: "Active subscription confirmed. Response contains the requested data.",
            },
          ].map((s) => (
            <div key={s.status} className="border border-divider rounded-md p-4 space-y-1">
              <div className="flex items-center gap-2">
                <span className={`font-mono text-sm font-bold ${s.color}`}>{s.status}</span>
                <span className="text-xs text-muted">{s.label}</span>
              </div>
              <p className="text-xs text-muted">{s.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Agent flow */}
      <section className="space-y-3">
        <h2 className="text-base font-semibold tracking-tight">Recommended agent flow</h2>
        <pre className="bg-surface border border-divider rounded-md p-4 text-xs font-mono overflow-x-auto whitespace-pre leading-relaxed text-foreground">{`1. Ask the user for their Job Seek user ID
   (Settings → API key, or Profile page)

2. Verify the key:
   GET ${base}/me
   Authorization: Bearer <userId>

   → 200: proceed to data calls
   → 402: go to step 3
   → 401: ask user to sign up at https://jobseek.colophon-group.org

3. If 402 — start a subscription:
   GET ${base}/checkout?userId=<userId>
   → returns { checkoutUrl }
   Open checkoutUrl in the user's browser, wait for completion.

4. Search jobs:
   GET ${base}/jobs?q=typescript&sen=senior&loc=berlin
   Authorization: Bearer <userId>

5. Fetch a specific job:
   GET ${base}/jobs/<id>
   Authorization: Bearer <userId>`}</pre>
      </section>

      {/* 402 response shape */}
      <section className="space-y-3">
        <h2 className="text-base font-semibold tracking-tight">402 response shape</h2>
        <pre className="bg-surface border border-divider rounded-md p-4 text-xs font-mono overflow-x-auto whitespace-pre leading-relaxed text-foreground">{`{
  "error": "Payment Required",
  "message": "An active Job Seek subscription is required to use this API.",
  "subscribe": "https://jobseek.colophon-group.org/en/pricing",
  "checkoutUrl": "${base}/checkout?userId=<id>&successUrl=...&cancelUrl=..."
}`}</pre>
        <p className="text-xs text-muted">
          The <code className="font-mono">checkoutUrl</code> is a pre-built link to{" "}
          <code className="font-mono">GET /api/checkout</code> — open it or redirect the user to it.
          It resolves to a Stripe hosted checkout page.
        </p>
      </section>

      {/* Auth / utility endpoints */}
      <section className="space-y-4">
        <h2 className="text-base font-semibold tracking-tight">Auth endpoints</h2>
        <EndpointList endpoints={authEndpoints} />
      </section>

      {/* Ghost job analysis */}
      <section className="space-y-4">
        <h2 className="text-base font-semibold tracking-tight">Ghost-job analysis</h2>
        <p className="text-xs text-muted -mt-2 max-w-2xl">
          Uses the Wayback Machine to reconstruct a company&apos;s full job posting history and detect
          roles that have been open for months with no apparent intention of being filled. No auth
          required for the open tier; paid tier preserves credits.
        </p>
        <EndpointList endpoints={ghostingEndpoints} />
      </section>

      {/* Data endpoints */}
      <section className="space-y-4">
        <h2 className="text-base font-semibold tracking-tight">Data endpoints</h2>
        <p className="text-xs text-muted -mt-2">All require <code className="font-mono">Authorization: Bearer &lt;userId&gt;</code>.</p>
        <EndpointList endpoints={dataEndpoints} />
      </section>

      {/* Utility endpoints */}
      <section className="space-y-4">
        <h2 className="text-base font-semibold tracking-tight">Utility endpoints</h2>
        <p className="text-xs text-muted -mt-2">No auth required.</p>
        <EndpointList endpoints={utilityEndpoints} />
      </section>

      {/* Notes */}
      <section className="space-y-2 border-t border-divider pt-6">
        <h2 className="text-base font-semibold tracking-tight">Notes</h2>
        <ul className="text-sm text-muted space-y-1 list-disc list-inside">
          <li>All responses are <code className="font-mono text-xs">application/json</code>.</li>
          <li>
            Use <code className="font-mono text-xs">moreAt</code> from search results to get the
            next page (it&apos;s a URL to the human explore page; paginated API coming soon).
          </li>
          <li>
            Filter slugs must come from <code className="font-mono text-xs">/api/taxonomies</code> —
            free-form strings are not supported.
          </li>
          <li>
            The <code className="font-mono text-xs">locale</code> param affects label text only;
            IDs and slugs are locale-independent.
          </li>
        </ul>
      </section>
    </div>
  );
}

type Endpoint = {
  method: string;
  path: string;
  summary: string;
  auth?: boolean;
  params: { name: string; type: string; required: boolean; desc: string }[];
  returns: string;
  example: string;
};

function EndpointList({ endpoints }: { endpoints: Endpoint[] }) {
  return (
    <div className="space-y-4">
      {endpoints.map((ep) => (
        <div key={ep.path} className="border border-divider rounded-md overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-3 bg-surface border-b border-divider">
            <span className="font-mono text-xs font-bold text-primary bg-primary/10 rounded px-1.5 py-0.5">
              {ep.method}
            </span>
            <code className="font-mono text-sm text-foreground">{ep.path}</code>
            <span className="text-muted text-xs">&mdash; {ep.summary}</span>
            {ep.auth === false && (
              <span className="ml-auto text-xs text-muted border border-divider rounded px-1.5 py-0.5">
                no auth
              </span>
            )}
          </div>

          <div className="p-4 space-y-4">
            {ep.params.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">Parameters</p>
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-divider text-left text-muted">
                      <th className="pb-1.5 pr-4 font-medium">Name</th>
                      <th className="pb-1.5 pr-4 font-medium">Type</th>
                      <th className="pb-1.5 pr-4 font-medium">Required</th>
                      <th className="pb-1.5 font-medium">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ep.params.map((p) => (
                      <tr key={p.name} className="border-b border-divider last:border-0">
                        <td className="py-1.5 pr-4 font-mono text-foreground">{p.name}</td>
                        <td className="py-1.5 pr-4 text-muted">{p.type}</td>
                        <td className="py-1.5 pr-4 text-muted">{p.required ? "yes" : "no"}</td>
                        <td className="py-1.5 text-muted">{p.desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-1">Returns</p>
              <code className="text-xs font-mono text-foreground">{ep.returns}</code>
            </div>

            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-1">Example</p>
              <pre className="bg-surface border border-divider rounded px-3 py-2 text-xs font-mono text-foreground overflow-x-auto whitespace-pre">
                {ep.example}
              </pre>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
