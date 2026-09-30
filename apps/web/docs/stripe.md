# Stripe Payments and Billing

Stripe replaces Paddle for new Job Seek Pro purchases. Pro unlocks Narrowed
results in watchlists only; search, ten watchlists, email alerts and application
tracking remain free. The historical `unlimited` plan identifier and independent
manual grants remain compatible. Paddle rejected verification; the operator
confirmed there are no Paddle customers to migrate.

The Stripe implementation planner was invoked and accepted on 30 September
2026 for hosted subscription Checkout, flat monthly pricing, a card-on-file
trial and the Customer Portal. SDK 22.6.2 pins API `2026-08-26.dahlia`, verified
against current Stripe documentation and the installed SDK.

## Configuration

Apply additive routine migration `0098_stripe_billing` after `0097_pro_waitlist`
before configuring Stripe. Use the repository's reviewed routine migration
workflow, commit/hash confirmation and exact ledger checks, never schema push.
The server-only tables enable RLS and revoke browser-role privileges, including
TRUNCATE. CI exercises the actual SQL against PostgreSQL 17 with inherited
Supabase-style grants and separately tests billing transactions in PostgreSQL.

```dotenv
STRIPE_ENVIRONMENT=sandbox
STRIPE_SECRET_KEY=<server-only sk_test_ or scoped rk_test_ key>
STRIPE_PRO_PRICE_ID=<matching monthly USD 10 Price, tax_behavior=exclusive>
STRIPE_PORTAL_CONFIGURATION_ID=<matching bpc_ configuration>
STRIPE_WEBHOOK_SECRET=<matching destination whsec_ signing secret>
STRIPE_CHECKOUT_ENABLED=false
```

`BETTER_AUTH_URL` supplies the trusted return origin. Sandbox allows localhost
HTTP; other origins require HTTPS. Production rejects sandbox configuration;
Vercel previews reject live configuration. Production uses `production` and
live credentials/resources. No publishable key, Stripe.js or browser SDK is
needed for hosted Checkout. Keep environment/credentials configured while
subscriptions exist; close sales using `STRIPE_CHECKOUT_ENABLED=false`.

Public pricing and structured offer availability use `stripeSignupOpen()`,
which validates the launch flag and non-secret configuration. The prebuilt
production workflow cannot read Vercel's write-only payment secrets, so public
prerendering must not depend on them. Billing actions still use
`stripeCheckoutEnabled()` and require matching server and webhook credentials.
Verify homepage and Narrowed pricing as well as billing after launch changes.

Both build tasks in `turbo.json` include the public Stripe configuration and
`VERCEL_ENV` in `env`. Turborepo's strict environment mode otherwise removes
those settings before Next.js prerenders, even when Vercel has supplied them.
These inputs also invalidate the build cache when signup availability or
billing mode changes. Verify through the deployment command,
`pnpm turbo run build --filter=@jobseek/web`, rather than only running the
web package's build directly. Payment secrets remain runtime requirements.

The initial sandbox validation used `Job Seek sandbox`, account
`acct_1Tvn7nAXSxyCkkob`, product `prod_VM1le5FzmYVHuL`, price
`price_1ULJiKAXSxyCkkobd2A4rk5y`, portal `bpc_1ULJkJAXSxyCkkob4wV4FBQy`.
The sandbox product uses the verified Tax Codes API category
`txcd_10701401` (Website Information Services - Personal Use). Sandbox Tax
settings use the seller address published in the policy documents. Live tax
classification, obligations and registrations need operator verification.

## Trust boundaries and lifecycle

Checkout authenticates the account and validates the server-selected Price:
USD 10, monthly interval one, quantity one, exclusive tax and matching mode.
Stripe Tax calculates applicable taxes. First subscribers receive a seven-day
trial with `payment_method_collection=always`; prior Stripe/Paddle subscriptions
consume trial eligibility. The portal permits invoices, payment and customer
updates, and cancellation at period end, with price/quantity changes disabled.

Account locks serialize checkout, reconciliation and deletion intent. The
customer mapping and exact Checkout parameters/idempotency token commit before
creating a payable session. Repeated clicks reuse the same session. A lost API
response or failed final DB commit recovers by enumerating the customer's
sessions and matching the durable attempt, including after Stripe's 24-hour
idempotency retention. Completed sessions block further checkout until their
webhook binds the subscription. Expired attempts rotate their durable token and retry safely without consuming a trial.

The raw-body `/api/stripe/webhook` route uses the official SDK signature verifier.
Invalid signatures return 400; configuration/provider/DB failures return 503 for
retry. Unknown events are acknowledged. Subscribe the destination to:

```text
checkout.session.completed
checkout.session.async_payment_succeeded
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
customer.subscription.paused
customer.subscription.resumed
invoice.paid
invoice.payment_failed
invoice.payment_action_required
```

Use the pinned API version for the destination. Initial binding requires a
completed, server-created Checkout Session matching customer, account reference,
subscription and environment. Metadata alone cannot grant access. Once bound,
each event retrieves current Stripe subscription state after taking the account
lock; duplicates, reordered snapshots and same-second events converge. The
stored timestamp records reconciliation time, not event order.

Only active/trialing subscriptions with the expected price, quantity one,
matching mode, no collection pause and an unexpired item billing period grant
access. Flexible-mode `cancel_at` and classic `cancel_at_period_end` retain
access until their effective end. Past due, unpaid, incomplete, paused,
canceled and expired subscriptions receive no Pro access. Manual grants remain
independent. Existing Paddle state/webhooks/deletion cleanup remain for historical
records; there is no new Paddle purchase UI or public payment-link checkout.

Before account deletion, persist deletion intent and revoke Stripe access,
expire all open customer sessions, and cancel all recurring subscriptions,
including creations whose DB commit failed. A checkout-completion race or
provider failure leaves the user intact for retry. Accounts billed in another
environment, and legacy Stripe IDs in the historical `subscription` table,
require reconciliation before deletion. They are never silently reinterpreted
as the new provider records.

## Seller and launch

This uses ordinary Stripe Payments/Billing. Viktor Shcherbakov is the seller,
responsible for tax obligations, support and refunds. Stripe Tax calculation
does not make Stripe the merchant of record. Managed Payments is a separate
reviewed product and is neither assumed approved nor enabled. Public terms,
privacy, refund contact and Checkout policy links reflect this in en/de/fr/it.

New purchases were disabled during the initial rollout. Before a live launch:

1. Verify live account onboarding, charge readiness, seller details, product tax
   classification and required tax registrations. Configure trial reminders,
   receipts and Smart Retries/recovery emails in Billing settings.
2. Create/verify the matching live USD 10 Price and portal configuration, publish
   the updated policies, and configure Checkout public support/legal details.
3. Apply the reviewed migration and deploy with scoped live server credentials,
   matching portal/Price and a live webhook secret. Verify real signed delivery
   and subscription lifecycle behavior at the deployed endpoint.
4. Enable checkout only with explicit launch authorization after verification.
   Update public offer availability as part of launch.

The operator subsequently authorized live setup and merging PR #10188. The
live account `acct_1ULJMYPL7AMlx8Ld` has charges and payouts enabled, active card
payments, submitted onboarding details and no currently due requirements.
Stripe Tax is active; no tax registrations are recorded. The live resources
are product `prod_VM2tiM4gAwfTaM`, price `price_1ULKofPL7AMlx8Ldeq2HJWd4`,
portal `bpc_1ULKozPL7AMlx8LdyOcK6pyu`, and webhook
`we_1ULKq8PL7AMlx8LdPeUdRxrH` at `https://jseek.co/api/stripe/webhook`.
The reviewed migration and a production deployment with checkout disabled
completed successfully. Live secrets are server-only Vercel production
variables and are never committed.

Live validation found that the account enables Managed Payments by default.
Every Checkout request explicitly sets `managed_payments.enabled=false` so
our ordinary Payments/Billing model and required policy consent remain valid.
Subscriptions marked as Managed Payments cannot grant Pro access. Do not rely
on account defaults for this product. Dashboard trial reminders, recovery
settings and required tax registrations remain operator settings.

### Regional payment methods and currencies

Production checkout was enabled after the reviewed deployment and signed
delivery checks. The operator subsequently requested wallets and regional
methods for the US, Switzerland and India, with local equivalents of USD 10.
The same approved recurring Price now has exclusive-tax currency options:
CHF 8.33 and INR 959.85, calculated from the ECB reference rates dated
29 September 2026. These are fixed regional prices, not a daily FX adjustment;
review changes before modifying the Price. Existing USD billing is unchanged.
Keep currency selection automatic in Checkout; do not infer currency from the
UI language. Stripe localizes the multi-currency Price using customer location.

Apple Pay, Google Pay, Link and Amazon Pay are enabled in the live default
payment configuration. Wallet display still depends on customer location,
browser/device and an eligible saved wallet card. Live subscription Checkout
probes offered card, Link, Amazon Pay and PayPal in USD; CHF additionally
offered TWINT and Klarna. A browser verified TWINT beside CHF 8.33/month, the
seven-day trial and required policy consent. All probe Sessions were expired
without completing a purchase. A session created without an explicit currency,
matching the app's selection behavior, switched from USD to CHF and displayed
TWINT after the browser opened it from Switzerland. Sandbox CHF/INR subscriptions retain the
approved Price ID and its USD 10 base Price object, so entitlement validation
continues to work without broadening the allowed products or prices.

UPI is enabled as a payment preference but its live capability is inactive
with `rejected.other`; it requires Stripe review before it can appear in INR
Checkout. INR probes currently offer card and Link. SEPA debit is inactive
pending `individual.verification.proof_of_liveness`. Do not report either
method as available until its capability becomes active. PayPal recurring
availability must likewise be verified for the customer and Checkout flow;
an account payment-method preference alone does not guarantee display.

## Verification

```sh
# Requires a disposable local database whose name includes fixture.
STRIPE_TEST_DATABASE_URL=postgresql://localhost/jobseek_stripe_fixture \
  pnpm exec vitest run src/lib/stripe app/api/stripe
pnpm exec tsx scripts/test-stripe-billing-pg.ts --database-url <fixture-url>
```

Integration tests exercise database locking, lost-response recovery, verified
binding, lifecycle state, manual grants, trial eligibility, localized return
paths, bad prices, mode isolation and deletion failures. Route tests sign real
raw bodies using the official SDK. Browser tests cover hosted redirects and the free/subscriber/waitlist UI.

A real sandbox request through the app's Checkout implementation created and
persisted a bound hosted Session; its browser page showed the seven-day trial,
USD 10 renewal, required payment details and policy consent. Automated card
completion stopped at Stripe's agent purchase instructions. The session was
expired; no checkout-completion delivery is claimed for that test.

A separate real Billing API trial with a synthetic Stripe test payment method
(`sub_1ULKEoAXSxyCkkobHwoj2OKG`) verified current subscription item periods and
payment-method retention. Its actual API event was replayed through the app's
HTTP handler using a local fixture signing secret after explicitly binding the
fixture subscription. Trial access, scheduled cancellation retention, a real
portal session, final cancellation revocation and deletion cleanup all passed.
The fixture subscription is canceled. This proves real payload/current-state
compatibility, not Stripe-origin webhook delivery or first Checkout binding.
The live setup checks below add provider-origin delivery evidence; completed
Checkout initial binding still requires an actual authenticated purchase.

The live deployment rejected an invalid signature and accepted a locally signed
fixture. A real live fixture subscription generated event
`evt_1ULL9xPL7AMlx8LdbRRLcBVk`, which Stripe delivered and acknowledged at the
configured production endpoint (`pending_webhooks=0`). The fixture customer was
unbound to any Job Seek account, so this checked delivery/signature handling,
not access granting. The subscription was immediately canceled without payment.
A separate live hosted Session explicitly opted out of Managed Payments and
showed the seven-day trial, monthly price and required policy consent. It was
expired without completing a purchase. All eight live terms/privacy pages
passed browser checks in en/de/fr/it.

References: [Checkout subscriptions](https://docs.stripe.com/billing/subscriptions/build-subscriptions?payment-ui=checkout&ui=stripe-hosted),
[Customer Portal](https://docs.stripe.com/customer-management/integrate-customer-portal),
[Webhook delivery](https://docs.stripe.com/webhooks),
[API versions](https://docs.stripe.com/api-versions),
[Stripe Tax](https://docs.stripe.com/tax/products-prices-tax-codes-tax-behavior).
