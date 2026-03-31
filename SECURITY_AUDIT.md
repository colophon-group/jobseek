# Security Audit — Jobseek

**Date:** 2026-03-31
**Scope:** `apps/web/` (Next.js 15, API routes)
**Branch:** `security`

---

## Summary

Eleven vulnerabilities were identified and fixed across two audit passes. Two are
critical (unauthenticated subscription-granting and unauthenticated actor
triggering), three are high severity (free access to paid features and business
intelligence), and six are medium severity (SSRF surface, timing side-channels,
missing brute-force protection, and a Stripe replay-attack window).

### Audit Pass 1 (2026-03-31)

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

### Audit Pass 2 (2026-03-31)

| # | Severity | File | Status |
|---|----------|------|--------|
| 10 | MEDIUM | `src/lib/admin/basic-auth.ts` (`matchesBasicAuthorization`) | Fixed |
| 11 | MEDIUM | `app/agentic/api/auth/login/route.ts` | Fixed |
| 12 | MEDIUM | `app/api/stripe/webhook/route.ts` (replay attack) | Fixed |

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

---

## Audit Pass 2 Findings

### 10. MEDIUM — Timing Side-Channel in `matchesBasicAuthorization`

**File:** `src/lib/admin/basic-auth.ts`

**Description:**
The `matchesBasicAuthorization` function compared the provided `Basic` token to
`ADMIN_SECRET` using the JavaScript `===` operator. String equality in V8
short-circuits on the first differing byte, leaking a timing signal that a
patient attacker could use to enumerate the secret character-by-character via a
remote timing oracle. The affected route is `POST /api/admin/meta/apify-import`
which triggers a Meta Careers Apify data import. This was the same class of
vulnerability fixed in `verifyAgentKey` in Pass 1 (finding #9).

**Proof of concept:**
Repeated requests with incrementally longer matching prefixes produce measurable
timing differences under a noise-averaging attack. With sufficient request volume
(typically thousands), the full secret can be extracted.

**Fix:**
Replaced `===` with `crypto.timingSafeEqual`, using the same padding strategy
(`padEnd(128)`) already used in `verifyGhostingAdminKey` and `verifyAgentKey`.
Added an explicit `scheme !== "Basic"` early-exit before the secret comparison.

---

### 11. MEDIUM — No Rate Limiting on Agentic Admin Login

**File:** `app/agentic/api/auth/login/route.ts`

**Description:**
`POST /agentic/api/auth/login` accepted unlimited password guesses with no
brute-force protection. An attacker could mount a high-throughput dictionary
or brute-force attack against `ADMIN_PASSWORD` without any throttling. A
successful guess yields a 7-day JWT session cookie granting access to all
agentic admin views. The main auth route at `/api/auth/[...all]` already had
rate limiting (10 req/60 s), but the agentic login had none.

**Attack scenario:**
```bash
# Enumerate common passwords or short secrets at full network speed
for pass in $(cat rockyou.txt); do
  curl -s -X POST https://jseek.co/agentic/api/auth/login \
    -H 'Content-Type: application/json' \
    -d "{\"password\":\"$pass\"}" | grep -q '"ok":true' && echo "FOUND: $pass" && break
done
```

**Fix:**
Added `agenticLoginLimiter` (5 requests per 15 minutes per IP, using Upstash
Redis sliding window) in `src/lib/rate-limit.ts` and applied it as the first
check in the login handler. Returns `429 Too Many Requests` with `Retry-After`
when the limit is exceeded. Falls through if Redis is unavailable to avoid a
denial-of-service on the login if Redis goes down.

---

### 12. MEDIUM — Stripe Webhook Missing Replay-Attack Protection

**File:** `app/api/stripe/webhook/route.ts`

**Description:**
The Stripe webhook signature verification (added in Pass 1) verifies the
HMAC-SHA256 signature correctly, but did not check the `t=` timestamp embedded
in the `stripe-signature` header. Stripe includes this timestamp to enable
replay-attack protection: a valid signed event from the past can be replayed
indefinitely because the server never checked whether the timestamp was recent.

**Attack scenario:**
An attacker who can observe a legitimate `checkout.session.completed` event
(e.g., via network interception, a compromised logging pipeline, or an
accidentally-logged header dump) could replay that event at any future time to
re-activate a subscription without paying again:

```bash
# Replayed captured webhook (valid signature, stale timestamp)
curl -X POST https://jseek.co/api/stripe/webhook \
  -H 'stripe-signature: t=1700000000,v1=<captured-valid-signature>' \
  -d '<captured-body>'
```

**Fix:**
Added a `±300 second` (5-minute) tolerance check in `verifyStripeSignature`.
The function now parses `t=<unix-seconds>` from the signature header, compares
it against `Date.now()`, and returns `false` if the absolute difference exceeds
300 seconds. This matches the protection provided by `stripe.webhooks.constructEvent()`
in the official Stripe SDK.

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
| `POST /api/admin/meta/apify-import` | HTTP Basic (`matchesBasicAuthorization`) — now timing-safe |
| `POST /agentic/api/auth/login` | Password check + rate limit |
| `POST /agentic/api/auth/logout` | N/A (clears cookie) |
| `GET /api/v1/*` | Rate-limited, public read API — appropriate |
| `GET/POST/DELETE /mcp` | Public MCP endpoint — only proxies to public/paywalled APIs; no auth bypass possible |

---

## Recommendations

1. **Enable Stripe SDK verification** — Once the `stripe` package is installed,
   replace the hand-rolled HMAC check with `stripe.webhooks.constructEvent()`
   which also validates the timestamp to prevent replay attacks within a 5-minute
   window. Remove the `STRIPE_WEBHOOK_SECRET` guard for the case where the env var
   is unset — production should fail hard.

2. **Rotate secrets** — `GHOSTING_ADMIN_SECRET`, `AGENT_API_KEY`, and
   `ADMIN_SECRET` should be rotated now that unauthenticated or timing-observable
   access existed in production. Any secret transmitted over the wire before
   these fixes should be considered potentially compromised.

3. **Add rate-limiting to agentic endpoints** — Even with auth in place, the
   ghosting and apify endpoints trigger expensive third-party operations. Consider
   adding per-user rate limits (e.g., max 5 actor runs / hour) to limit damage
   from a compromised credential.

4. **Review Apify dataset visibility** — Apify datasets created by the actor runs
   may be publicly readable via the Apify platform if the actor was not configured
   with `DATASET_IS_PUBLIC: false`. Audit Apify actor settings.
