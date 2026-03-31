# Security Audit — Jobseek

**Date:** 2026-03-31
**Scope:** `apps/web/` (Next.js 15, API routes)
**Branch:** `security`

---

## Summary

Eight vulnerabilities were identified and fixed in this audit pass. Two are critical
(unauthenticated subscription-granting and unauthenticated actor triggering), three
are high severity (free access to paid features and business intelligence), and three
are medium severity (SSRF surface and a timing side-channel).

| # | Severity | File | Status |
|---|----------|------|--------|
| 1 | CRITICAL | `app/api/stripe/webhook/route.ts` | Fixed |
| 2 | CRITICAL | `app/agentic/api/apify/run/route.ts` | Fixed |
| 3 | HIGH | `app/agentic/api/signals/route.ts` | Fixed |
| 4 | HIGH | `app/agentic/api/ghosting/route.ts` | Fixed |
| 5 | HIGH | `app/agentic/api/ghosting/[runId]/route.ts` | Fixed |
| 6 | HIGH | `app/agentic/api/ghosting/batch/route.ts` | Fixed |
| 7 | MEDIUM | `app/agentic/api/apify/status/[runId]/route.ts` | Fixed |
| 8 | MEDIUM | `app/agentic/api/hiring-cafe/route.ts` | Fixed |
| 9 | MEDIUM | `src/lib/agentic/agentAuth.ts` (`verifyAgentKey`) | Fixed |

---

## Findings

### 1. CRITICAL — Stripe Webhook Signature Verification Commented Out

**File:** `app/api/stripe/webhook/route.ts`

**Description:**
The Stripe HMAC-SHA256 signature check was entirely commented out. The endpoint
parsed the raw request body as JSON and immediately processed any event payload,
including `checkout.session.completed`. This allowed anyone who could POST a
crafted JSON body to promote any user account to an unlimited-plan active
subscription at zero cost.

**Proof of concept:**
```bash
curl -X POST https://jseek.co/api/stripe/webhook \
  -H 'Content-Type: application/json' \
  -d '{"type":"checkout.session.completed","data":{"object":{"metadata":{"userId":"<victim-id>"},"customer":"cus_fake","subscription":"sub_fake"}}}'
```

**Fix:**
Implemented HMAC-SHA256 signature verification using the Web Crypto API and
`crypto.timingSafeEqual`. The endpoint now reads the raw request body as text,
extracts the `stripe-signature` header, recomputes the HMAC over
`<timestamp>.<body>`, and rejects any request where the computed digest does not
match a `v1=` value in the header. If `STRIPE_WEBHOOK_SECRET` is not set the
endpoint returns 503 rather than falling back to unverified processing.

---

### 2. CRITICAL — Unauthenticated Apify Orchestrator Trigger

**File:** `app/agentic/api/apify/run/route.ts`

**Description:**
`POST /agentic/api/apify/run` triggered an Apify actor run with zero
authentication. Apify actor runs consume API credits proportional to compute
time (CPU, memory, network). Any internet user could trigger unbounded runs
against the project's Apify account, exhausting credits and incurring billing
charges.

**Fix:**
Added `verifyGhostingAdminKey` guard (timing-safe check against
`GHOSTING_ADMIN_SECRET`). Callers must supply
`Authorization: Auth Bearer Basic <secret>`.

---

### 3. HIGH — Unauthenticated Hiring-Signal Business Intelligence Exposure

**File:** `app/agentic/api/signals/route.ts`

**Description:**
`GET /agentic/api/signals` returned up to 200 rows from the `hiringSignal` table
with no authentication. The table contains proprietary analysis: signal type,
score, AI reasoning, and metadata per company — the core paid-tier deliverable.
This data was freely readable by any unauthenticated request.

**Fix:**
Added `verifyGhostingAdminKey` guard. The endpoint now requires
`Authorization: Auth Bearer Basic <GHOSTING_ADMIN_SECRET>`.

---

### 4. HIGH — Unauthenticated Ghost-Job Analysis Trigger

**File:** `app/agentic/api/ghosting/route.ts`

**Description:**
`POST /agentic/api/ghosting` triggered an Apify wayback-job-history actor run
with no authentication. The paid endpoint at `/ghosting/paid` enforced the
paywall, but the base route had none, making the paywall trivially bypassable by
any client that simply called the base URL.

**Fix:**
Added `checkPaywall` guard (identical to `/ghosting/paid`). Both the free-tier
and paid-tier paths now require a valid Bearer token.

---

### 5. HIGH — Unauthenticated Ghost-Job Result Polling

**File:** `app/agentic/api/ghosting/[runId]/route.ts`

**Description:**
`GET /agentic/api/ghosting/:runId` returned the full ghost-job analysis
(ghost rate, AI summary, per-role scores) for any run ID, with no
authentication. An attacker who triggered a run via any path — or guessed/
enumerated a run ID from the Apify orchestrator — could retrieve the paid-tier
analysis for free.

**Fix:**
Added `checkPaywall` guard. Callers must present a valid Bearer token before
run results are returned.

---

### 6. HIGH — Unauthenticated Ghost-Job Batch Trigger

**File:** `app/agentic/api/ghosting/batch/route.ts`

**Description:**
`POST /agentic/api/ghosting/batch` accepted 1–10 company entries and launched a
separate Apify actor run per entry, with no authentication. This is a force
multiplier: a single request could spawn 10 parallel actor runs, each consuming
Apify credits for the duration of the analysis (typically 5–30 minutes each).

**Fix:**
Added `verifyGhostingAdminKey` guard (admin-only, matching
`/ghosting/admin`).

---

### 7. MEDIUM — Unauthenticated Apify Run-Status Enumeration

**File:** `app/agentic/api/apify/status/[runId]/route.ts`

**Description:**
`GET /agentic/api/apify/status/:runId` proxied Apify run-status API calls with
no authentication. Any caller who knew (or guessed) a run ID could learn run
metadata and, depending on the Apify actor, retrieve dataset contents.

**Fix:**
Added `checkPaywall` guard. Run-status queries now require a valid Bearer token.

---

### 8. MEDIUM — Unauthenticated External API Proxy (SSRF / Resource Abuse)

**File:** `app/agentic/api/hiring-cafe/route.ts`

**Description:**
`POST /agentic/api/hiring-cafe` proxied requests to `hiring.cafe` and
`web.archive.org` with no authentication. The route is a server-side proxy:
it makes outbound HTTP calls on behalf of the caller. Without authentication
any internet user could:

- Enumerate company data from `hiring.cafe` at scale via the server's IP,
  avoiding client-side rate-limiting.
- Cause the server to make a large number of Wayback Machine CDX requests,
  potentially triggering IP-level blocks for the entire application.

**Fix:**
Added `checkPaywall` guard.

---

### 9. MEDIUM — Timing Side-Channel in `verifyAgentKey`

**File:** `src/lib/agentic/agentAuth.ts`

**Description:**
The `verifyAgentKey` function compared the provided token to `AGENT_API_KEY`
using the JavaScript `===` operator. String equality in V8 short-circuits on
the first differing byte, leaking the secret length and a timing signal that
can be exploited by a sufficiently patient attacker to enumerate the key
character-by-character via a remote timing oracle.

**Fix:**
Replaced `===` with `crypto.timingSafeEqual` (using the same padding strategy
already present in `verifyGhostingAdminKey`). Both branches of agentAuth now
use constant-time comparison.

---

## Routes Audited — No Issues Found

The following routes were reviewed and found to have appropriate access controls
already in place:

| Route | Auth mechanism |
|-------|---------------|
| `POST /agentic/api/ghosting/admin` | `verifyGhostingAdminKey` |
| `GET /agentic/api/ghosting/admin/:runId` | `verifyGhostingAdminKey` |
| `POST /agentic/api/discovery/trigger` | `verifyGhostingAdminKey` |
| `GET /agentic/api/discovery` | `verifyGhostingAdminKey` |
| `GET /agentic/api/companies` | `checkPaywall` |
| `GET /agentic/api/jobs` | `checkPaywall` |
| `GET /agentic/api/jobs/:id` | `checkPaywall` |
| `GET /agentic/api/me` | `checkPaywall` |
| `GET /agentic/api/ping` | `checkPaywall` |
| `POST /agentic/api/ghosting/paid` | `checkPaywall` |
| `GET /agentic/api/ghosting/paid/:runId` | `checkPaywall` |
| `POST /api/admin/meta/apify-import` | HTTP Basic (`matchesBasicAuthorization`) |
| `POST /agentic/api/auth/login` | Password check (`checkPassword`) |
| `POST /agentic/api/auth/logout` | N/A (clears cookie) |
| `GET /api/v1/*` | Rate-limited, public read API — appropriate |

---

## Recommendations

1. **Enable Stripe SDK verification** — Once the `stripe` package is installed,
   replace the hand-rolled HMAC check with `stripe.webhooks.constructEvent()`
   which also validates the timestamp to prevent replay attacks within a 5-minute
   window. Remove the `STRIPE_WEBHOOK_SECRET` guard for the case where the env var
   is unset — production should fail hard.

2. **Add replay-attack protection to the webhook** — The current fix verifies the
   HMAC but does not check the `t=` timestamp. Add a check that the event
   timestamp is within ±300 seconds of `Date.now()` to prevent replayed valid
   signatures.

3. **Rotate secrets** — `GHOSTING_ADMIN_SECRET` and `AGENT_API_KEY` should be
   rotated now that unauthenticated access existed in production. Any secret that
   was ever transmitted over the wire to these endpoints prior to this fix should
   be considered compromised.

4. **Add rate-limiting to agentic endpoints** — Even with auth in place, the
   ghosting and apify endpoints trigger expensive third-party operations. Consider
   adding per-user rate limits (e.g., max 5 actor runs / hour) to limit damage
   from a compromised credential.

5. **Review Apify dataset visibility** — Apify datasets created by the actor runs
   may be publicly readable via the Apify platform if the actor was not configured
   with `DATASET_IS_PUBLIC: false`. Audit Apify actor settings.
