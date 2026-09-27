# Weekly watchlist email notifications

The implementation of [#8317](https://github.com/colophon-group/jobseek/issues/8317)
uses the existing Resend account and sender. Alerts are opt-in and available
without a subscription. Only verified owners receive mail. One weekly digest
combines enabled watchlists, deduplicates postings and shows at most 20 roles.
Matching uses current structured watchlist filters, independently of Jev results.

Settings provides a global pause. It preserves per-watchlist choices, blocks
changes to those choices while paused, and establishes a new matching floor on
resume. Account verification/password-reset messages are unaffected. The
watchlist control explains the pause on hover/focus and opens a full-screen
warning on mobile, with a link to Settings and focus restoration on dismissal.

## Runtime and activation

Apply `0095_notification_delivery_quota` using the reviewed routine migration
workflow before enabling the runner. It adds server-only UTC quota buckets and
lets an unsent, definitively rejected delivery later finish as an empty window
without erasing its attempt history. The existing `0088` migration is required.

Configure only production through Vercel environment management:

| Variable | Behavior |
| --- | --- |
| `JOB_ALERTS_MODE` | `off` (default), `shadow`, `internal`, `live` |
| `JOB_ALERTS_DAILY_CAP` | Default and maximum 75 attempts; 0 disables sends |
| `JOB_ALERTS_MONTHLY_CAP` | Default and maximum 2400 attempts; 0 disables sends |
| `JOB_ALERTS_INTERNAL_USER_IDS` | Comma-separated owner IDs required in `internal`; these users receive only their own matches |
| `CRON_SECRET` | Bearer authentication for the cron route |
| `JOB_ALERTS_UNSUBSCRIBE_SECRET` | Dedicated random signing secret, at least 32 characters |
| `RESEND_API_KEY` | Existing Resend credential |
| `RESEND_WEBHOOK_SECRET` | Signing secret for the notification webhook |

Every variable is included in the two Turbo web build allowlists. Preview
Vercel deployments reject `internal`/`live`. Do not copy production credentials
into UI fixtures or preview projects. No provider install, upgrade or additional
paid resource is needed by this code.

The rollout order is `off` → `shadow` → `internal` → `live`. The issue's recorded
rendered-UI/email approval and provider/cost-envelope approval are still the
production activation gates. A merged PR or passing tests does not itself
activate sending. The proposed envelope is at most 75 daily / 2400 monthly
notification attempts, with no tier change or overage enablement. Verify actual
account limits and account-email volume before approval; the implementation
reserves capacity by limiting job mail, not by metering unrelated applications.

Register the Resend webhook at `https://jseek.co/api/notifications/webhook` for
`email.sent`, `email.delivered`, `email.delivery_delayed`, `email.failed`,
`email.bounced`, `email.complained`, and `email.suppressed`. Use the signing secret
for this exact endpoint. Configure and test the webhook before internal sending.
Bounces, complaints and suppression pause future job mail for the current
recipient; replaying the same event does not undo an intentional later resume.

## Scheduling, limits and recovery

Vercel Cron calls `/api/internal/job-alerts` daily at 03:00 UTC. Stable per-owner
weekday/hour slots are evaluated across the preceding seven days, covering a
missed daily invocation. Both `sent` and zero-match `skipped` windows advance the
floor. Pauses and fresh opt-ins impose later floors, preventing catch-up mail.

A Redis owner-token lock excludes overlapping runners; database claims and
uniqueness remain the correctness boundary. Sweeps process ten owners per page,
with matching concurrency two and provider requests serialized at a bounded
rate. A saved cursor resumes page boundaries after the 220-second work budget.
The endpoint reports `continuation` if more work remains; an authenticated manual
invocation resumes it, and the next day's invocation resumes unfinished owner pages before starting a new sweep. This prevents later owners from starving when a sweep exceeds the daily runtime budget. Redis
state loss can cause rescanning, not duplicate sending or quota resets.

Quota reservation and the transition to `unknown` commit in one database
transaction before calling Resend. Separate transactions serialize the final
eligibility check and submission with pause/toggle/unsubscribe. Current user,
preference and watchlist changes invalidate a stale plan. Uncertain transport,
5xx, or malformed provider responses remain `unknown` and are never automatically
resent, even after the provider's idempotency TTL. Definitive rejections have a
three-attempt lifetime limit; failed and ambiguous attempts keep reservations.

Signed webhook tags bind acceptance evidence to the delivery and attempt.
Provider acceptance is recorded as `sent`; an asynchronous bounce/failure is
recorded in `last_error_code` without sending the same digest again. Webhook
database failures return 503 so Resend retries. To resolve a lost response,
replay the matching signed event from Resend. If no evidence exists, leave the
row unknown and investigate: do not reset it speculatively. Exhausted definitive
failures also require investigation before operator-authorized recovery.

`job_alerts_run` logs contain aggregate counts and duration only. Responses also
report remaining daily/monthly quota. Never log a plan, request payload, email
address, user ID, job title, raw provider error, or unsubscribe token. Completed
rows are retained for 400 days and cleaned in bounded batches; unresolved rows
are preserved. Deleting an account cascades its delivery rows.

Unsubscribe uses an opaque signed delivery token bound to the recipient's
current email. GET only renders confirmation; RFC 8058 POST pauses job emails
without login. Both routes are uncached, and the confirmation page suppresses
referrers and indexing. Email changes invalidate old tokens. Rotating the signing
secret invalidates prior unsubscribe links, so keep it stable during rollout.

## Verification

- `pnpm exec vitest run src/lib/notifications/__tests__ src/lib/services/__tests__/notification-*.test.ts app/api/notifications app/api/internal/job-alerts`
- `NOTIFICATION_TEST_DATABASE_URL=postgres://…@127.0.0.1:PORT/notification_fixture pnpm exec vitest run src/lib/services/__tests__/notification-delivery-pg.test.ts`
- `pnpm exec tsx script/preview-notification-email.ts /tmp/notification-review`

The PostgreSQL suite intentionally recreates the public schema and accepts only
an explicit localhost database ending in `_fixture`. It exercises the actual
migrations, claims, transactions, quota races, opt-outs and webhook reconciliation;
only the external email call is replaced. Ordinary test runs skip this suite.
Email preview generation is offline, contains fictional roles, and sends nothing.
