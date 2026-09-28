# Paddle billing

## Product and account configuration (27 September 2026)

Pro unlocks **Narrowed results for watchlists only**. Standard search, up to ten
watchlists, email alerts, and application tracking are available to everyone.
The historical internal `unlimited` plan name represents Pro; it does not
remove the account-wide watchlist limit.

Both Paddle environments have a Job Seek Pro SaaS product, monthly USD 10
pricing, quantity fixed to one, and a **seven-day trial requiring a payment
method**. After the trial, billing renews monthly. A second price without a
trial is used when the same account subscribes again. Taxes follow Paddle's
location-based calculation and the final amount is displayed at checkout.

| Resource | Sandbox | Live |
| --- | --- | --- |
| Product | `pro_01m3hbqgzjvtmcprxxerpqbbap` | `pro_01m3hb8kczagczm3tgna18c6qk` |
| Trial price | `pri_01m3hbqhecvxfgtjqrx7te9taq` | `pri_01m3hb8kk4pcfh7fgcf8r97v08` |
| Returning price | `pri_01m3hbqhn6nas4gndh5zcd2cqz` | `pri_01m3hbc6dk8hzhjvd78j5n8atx` |
| Client token ID (not token value) | `ctkn_01m3hbqj2xj8hfmzsw8r2se085` | `ctkn_01m3hb8kryh4befmff43tehg4g` |

The operator configured the sandbox default payment link to
`http://localhost:3100/en/checkout`. Paddle returns its generated links with
HTTPS; local HTTP testing opens the same path and `_ptxn` parameter directly.
For deployed environments use an HTTPS URL. The payment page initializes
Paddle.js, which handles `_ptxn` automatically.

## Stripe retirement (28 September 2026)

Paddle replaces Stripe. The Stripe webhook route and SDK have been removed;
`/api/paddle/webhook` is the billing notification endpoint. The application no
longer reads Stripe API keys or webhook secrets. Historical Stripe columns and
migrations remain for data compatibility, and manual grants in `subscription`
continue to participate in paid-entitlement checks. This source change does
not alter provider dashboard settings or deployed secrets.

## Runtime configuration

Set these on the web application, separately for each environment:

```dotenv
PADDLE_ENVIRONMENT=sandbox
PADDLE_API_KEY=<server-only sandbox API key>
PADDLE_PRO_PRICE_ID=pri_01m3hbqhecvxfgtjqrx7te9taq
PADDLE_PRO_RETURNING_PRICE_ID=pri_01m3hbqhn6nas4gndh5zcd2cqz
PADDLE_WEBHOOK_SECRET=<secret for this notification destination>
NEXT_PUBLIC_PADDLE_ENVIRONMENT=sandbox
NEXT_PUBLIC_PADDLE_CLIENT_TOKEN=<public test_ client-side token>
PADDLE_CHECKOUT_ENABLED=false
```

Live uses `production`, its own price IDs, a `pdl_live_` server key, a `live_`
client token, and a separate notification secret. Production deployments reject
sandbox configuration. Keep PADDLE_ENVIRONMENT configured while subscriptions exist; disable new purchases with PADDLE_CHECKOUT_ENABLED, not by removing billing configuration.

Public environment variables are built into the browser
bundle: rebuild when changing them. No API keys or webhook secrets belong in
browser bundles or Git. Local sandbox and live credentials are in ignored,
mode-0600 files; the current checkout flag is false.

API permissions needed by the application: `transaction.read`,
`transaction.write`, `customer_portal_session.write`, `subscription.read`, and
`subscription.write` (account-deletion cancellation). Catalog and notification
administration permissions are setup-only; use a scoped runtime key at launch.

## Persistence and access

Apply additive migration `0096_paddle_billing` before enabling Paddle runtime
configuration. It is registered in the routine migration journal and registry.
Do not run schema push against production. Follow the existing routine migration
workflow and bind its confirmation to the reviewed commit and SQL hash.

`paddle_account` separates users/customers by environment and stores the pending
transaction, selected price, and consumed trial. `paddle_subscription` stores
provider state, exact expected price, entitlement, and event time. Existing
manually granted subscriptions remain valid. Both Paddle tables enable row-level
security without browser policies and revoke all table privileges from PUBLIC,
`anon`, and `authenticated`; only trusted server database access manages billing.
A disposable PostgreSQL harness applies the actual SQL under Supabase-style
default grants and proves browser roles cannot read, modify, or truncate these
tables while the server owner retains access.

Authenticated server checkout selects the price and serializes against the
account row. Repeated clicks reuse the same pending checkout. Completed
transactions awaiting provisioning block further purchases. Binding requires a
signed `subscription.created` event whose transaction matches the stored
transaction; arbitrary user metadata cannot grant access.

The raw-body webhook at `/api/paddle/webhook` verifies signatures through the
initialized official Node SDK. Invalid signatures return 400; configuration or
processing failures return 503 so Paddle can retry. Persisted provider timestamps
retain microseconds. Account locks and timestamp comparisons handle duplicates
and reordered delivery. Updates arriving before initial creation are retried.

Subscribe the notification destination to:

```text
subscription.created
subscription.updated
subscription.activated
subscription.trialing
subscription.past_due
subscription.paused
subscription.resumed
subscription.canceled
```

Use `traffic_source=platform` for live. Configure the returned endpoint secret
on the matching deployment before enabling checkout. The local test secret is
not a registered Paddle notification secret. The production destination `ntfset_01m3hftn9gn84nsnwqd5gfeb8m` is registered
for `https://jseek.co/api/paddle/webhook`; its signing secret is configured in
Vercel. It is paused until the production handler is deployed. Checkout is
disabled in the staged production configuration.

Only active/trialing subscriptions with the configured price, quantity one,
and an unexpired period grant AI access. Scheduled cancellation retains access
until the period ends. Past due, paused, canceled, and expired subscriptions do
not grant AI access. All AI configuration/execution and bootstrap readers share
this rule. Email alerts have no paid-plan restriction.

Public pricing and subscription settings share the Narrowed example and describe
the outcome, without marketing AI. Anonymous visitors can inspect the offer before
signing in; sign-in preserves both the billing page and the original search return
path. Subscribers see status, dates, and a watchlist entry point instead of the
purchase pitch. Interrupted subscribers are sent to billing management, and
returning subscribers see the monthly price without another trial.

The billing portal remains available after access expires. Account deletion
first records deletion intent (blocking concurrent checkout), cancels pending
checkout and recurring subscriptions, and only then permits data deletion.
Provider failure leaves the account intact and allows retry. Deletion is
immediate cancellation, unlike normal portal end-of-period cancellation.

## Verification

Validation: 357 tests passed across 30 affected suites, the production build completed, and the routine migration registry/hash check passed. The SQL migration was applied to an isolated local PostgreSQL fixture. Integration
tests cover authenticated checkout, duplicate clicks, metadata spoofing, event
ordering, microseconds, trial/active/expired access, cancellation, portal access,
manual grants, sandbox isolation, and account deletion. Route tests use actual
HMAC signatures and the official SDK verifier. UI tests check server-selected
transactions and the preserved return path.

Run the database suite only against a disposable local database whose name
contains `fixture` (the suite truncates billing tables):

```sh
PADDLE_TEST_DATABASE_URL=postgresql://localhost/jobseek_paddle_fixture \
  pnpm exec vitest run src/lib/paddle app/api/paddle
```

A real sandbox Paddle.js checkout completed with Paddle's test card:
`txn_01m3hcfb9wx7qw52gg5a1f53ry`, subscription
`sub_01m3hcyfjwyjdrss4whg0jn43s`. Paddle reported `trialing` with a seven-day
period, 27 September–4 October 2026. The provider's events were retrieved via API
and replayed through the local HTTP webhook with a local test signature. The trial events granted access and the cancellation events revoked it; an authenticated sandbox customer portal session was also created. The operator confirmed receipt of both trial and cancellation emails. This first test checked actual payload compatibility and persistence.

On 27 September, Paddle also delivered seven signed simulator events over public
HTTPS through a temporary webhook-only tunnel to the local fixture. Creation,
trialing, activation, past-due, recovery, scheduled cancellation, and final
cancellation all completed with HTTP 200 and the expected persisted access state.
The temporary sandbox destination (`ntfset_01m3hg0ptsskke6x6zkv4nrdvk`) was disabled
after verification. This proves Paddle-origin signature and delivery compatibility;
production destination health still needs verification after deployment.

The launch preparation updates the public terms, refund section, checkout policy
links, privacy disclosures, and all four locales. Production-build browser checks
confirmed the policy pages and links render in each locale. The migration fixture
count and routine-migration test now bind the reviewed 0095 head.

## Remaining launch work

Live checkout stays disabled. The API reported account setup, domain review,
business identification, identity verification, and final review pending. The
operator must complete these with the actual seller details in Paddle.

Before launch:

1. Deploy the updated public terms, privacy disclosures, and refund section
   at `/en/terms#refund-policy`, then submit `jseek.co` for Paddle domain approval.
2. Deploy the reviewed code and migration, install scoped live credentials, and
   set the live default payment link to `https://jseek.co/en/checkout`.
3. Activate the registered production webhook after deployment and verify
   delivery and signature handling at the deployed endpoint.
4. Enable `PADDLE_CHECKOUT_ENABLED=true` only after verification. Update structured offer availability as part of launch; the homepage already
   links to the new Pro offer.

## References

- [Trials](https://developer.paddle.com/build/trials/create-trial/)
- [Default payment link](https://developer.paddle.com/build/transactions/default-payment-link/)
- [Notification setup](https://developer.paddle.com/api-reference/notification-settings/create-notification-setting/)
- [Webhook signatures](https://developer.paddle.com/webhooks/about/signature-verification/)
- [Sandbox test cards](https://developer.paddle.com/sdks/sandbox/)
- [Domain review](https://www.paddle.com/help/start/account-verification/what-is-domain-verification)
