# Narrowed maintenance and subscription controls

The incident controls live in `apps/web/src/config/service-availability.json`.
The source controls support independent containment and staged recovery:

| Control | Effect |
| --- | --- |
| `narrowedMaintenance` | Blocks owner configuration writes and demand; suppresses foreground evaluation/polling and shows maintenance notices. Saved criteria and decisions remain stored. |
| `narrowedWorkerPaused` | Stops shared worker execution independently of the public maintenance banner. This permits recovery verification before reopening. Both runtime execution switches must also be enabled. |
| `subscriptionPurchasesPaused` | Independently closes new Pro purchases. Narrowed maintenance also closes purchases even if this flag is false. |
| `subscriptionBillingPaused` | Desired existing Stripe collection state for the explicit billing operator command. Editing this file alone does **not** pause provider billing. |

These are deployment-scoped controls shared by server and browser. Change them
through a PR and the normal production deployment, then verify the deployed
behavior. Cached clients are also rejected by server admission. Keep payment
webhooks and billing portal access available. Do not rewrite subscription
records or delete saved Narrowed criteria to implement maintenance.

## Pause

1. Enable the appropriate source controls and deploy through
   `deploy-web-production.yml`. Keep `STRIPE_CHECKOUT_ENABLED=false`,
   `AI_FILTER_ENABLED=false` and `AI_FILTER_JEV_1_13_0_ENABLED=false` in the
   production Vercel project as additional gates. Environment changes require
   a new deployment; `rebuild=true` rebuilds current main through the normal
   serialized deployment workflow.
2. Dispatch `deploy-ai-filter-refresh.yml` on current main with exact `revision`
   and `action=pause`. It disables and stops only the Narrowed refresh timer
   and service, using existing protected, host-key-pinned SSH credentials.
   It defaults to pause and does not require the refresh bearer. Verify the
   timer is disabled and both units inactive. Web deployment does not resume it.
3. Audit existing payment subscriptions using an authorized payment runtime:

   ```bash
   pnpm --dir apps/web billing:control audit
   ```

   Set `STRIPE_ENVIRONMENT=production`, the existing protected live
   `STRIPE_SECRET_KEY` and `STRIPE_PRO_PRICE_ID` in that runtime. Never print or
   commit these values. A Vercel Secret cannot be downloaded with env pull/run;
   missing protected credentials means inventory is unverified, not zero.
4. If existing billing must pause, keep `subscriptionBillingPaused=true` and
   run `billing:control pause` in the same protected runtime. The operation is
   bounded to 1,000 inspected subscriptions and the configured Jobseek price.
   It re-reads records before writes, refuses complex/scheduled subscriptions,
   preserves existing independent pauses, and marks its own pauses in metadata.
   Output contains only aggregate counts. Serialize operator runs and prevent
   concurrent subscription changes while applying; Stripe updates have no
   conditional compare-and-swap guarantee.

The billing command pauses collection indefinitely with `behavior=void`, so
future pause-period invoices are voided instead of accumulating later debt.
It does not cancel subscriptions, change prices or automatically resume.
Existing entitlement logic treats paused collection as ineligible; webhooks
continue to reconcile that state. Invoices created before the pause can still
retry and require a separate invoice decision. Already-open Checkout sessions
can still complete: disabling application checkout does not expire provider
sessions. Inspect and expire those separately before declaring a complete sales
freeze. See [Stripe collection pause](https://docs.stripe.com/billing/subscriptions/pause-payment).

Email updates keep their existing independent daily cron, provider admission,
delivery windows, quotas, cooldown and idempotency controls. None of these
maintenance flags pauses standard job emails. Email preparation never enqueues
classification or spends AI budget. Narrowed-only opt-ins wait for completed
coverage and retain incomplete windows for retry; they never fall back to broad
results. Existing accepted, covered decisions remain eligible during maintenance.
Worker coverage progress must preserve the configuration edit timestamp so it
does not invalidate pending email plans for unchanged criteria. Actual criteria
edits and feedback still invalidate stale plans before submission.

Already-created Workflow runs belong to their original deployment; new source
or environment gates do not prove those runs stopped. Migration 0104 invalidates
their segment leases and rejects future claims at the database boundary. Retain
a bounded inventory for later cleanup using actual run identities.
Do not bypass Workflow quota enforcement or mutate the crawler writers.

## Resume

Resume requires an explicit operator decision after the incident acceptance
criteria below are satisfied. The operator authorized implementation and service
restoration on 2026-10-11. Paid-plan upgrades and alerts remain outside scope.

1. Clear `narrowedMaintenance` in a reviewed PR; retain purchase/billing pauses
   as needed. Restore the existing execution switches only when reopening is
   authorized. Deploy and prove current-revision progress in a bounded feed.
2. Dispatch the timer workflow with `action=activate` and exact current main.
   Activation fails while `narrowedWorkerPaused` is enabled. Public maintenance
   can remain on during worker recovery verification.
3. To resume billing, explicitly clear `subscriptionBillingPaused` and run
   `billing:control resume`. It clears only indefinite `void` pauses carrying
   this tool's ownership marker. It preserves foreign and scheduled pauses.
4. Clear `subscriptionPurchasesPaused` and restore `STRIPE_CHECKOUT_ENABLED`
   separately, deploy, then verify checkout availability. Clearing only source
   controls cannot override the existing disabled execution/checkout switches.

The redesign must use fixed project-wide admission, with watchlists as durable
work records. It must not create a Workflow/child Workflow/hook/recurring loop
per watchlist. If bounded Vercel orchestration cannot meet that contract, use a
dedicated resource-limited worker on the Hetzner backend. Idle watchlists must
produce zero Workflow runs.

## Shared-worker recovery after migration 0104

1. Keep the public maintenance and purchase controls on. Deploy migration 0104
   through the exact-head routine migration workflow. Verify the singleton row,
   demand table and enabled legacy-worker fence. The migration preserves criteria
   and decisions, and invalidates old processing leases. Cancellation of old
   Workflow records is cleanup, not the execution safety boundary after this fence.
2. Enable the two production AI runtime switches, leave `narrowedWorkerPaused`
   false, and deploy via the normal protected web production workflow. No plan
   upgrade or Workflow quota restoration is required.
3. Use timer `action=verify`, with exact current main and the affected watchlist
   UUID, to capture current revision, fingerprint, language scope, counts and
   fence receipt. It is read-only and logs no query text or owner identity.
4. Activate the timer through `action=activate`. Inspect bounded service receipts
   and repeat `action=verify` after ticks. Require actual reviewed decisions and
   current-criteria progress; a successful HTTP trigger is insufficient.
5. Only then reopen the public maintenance and purchase controls in a second PR
   and deploy. Existing billing uses the explicit operator command independently;
   changing its desired-state flag alone never mutates provider subscriptions.

The project worker admits at most one tick per 55 seconds, at most five serial
five-candidate checkpoints and one repair scope per tick. It has a 42-second
work deadline and 70-second crash leases. Three consecutive failures open a
15-minute shared circuit; per-work retries back off 5 minutes to 1 hour.
The host HTTP runner has separate systemd CPU/memory/task limits and no database
credentials or Docker socket. No crawler writers are restarted.

Freshness currently uses bounded rotating repair, including late indexing and
expired decisions. Per-posting commit signals and re-evaluation of enriched
unexpired postings remain tracked follow-ups. Larger scope counts increase
repair/backlog latency, never managed Workflow run count. Reopening must not
advertise one-minute freshness.
