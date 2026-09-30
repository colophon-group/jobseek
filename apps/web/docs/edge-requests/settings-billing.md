# Billing settings (`/:lang/settings/billing`)

The `(app)` layout supplies the shared application shell. The billing page
loads authenticated plan information in its dynamic subtree. See
[the Stripe runbook](../stripe.md) for configuration and rollout.

`getPlanInfo()` reads the shared paid-entitlement rule, environment-specific
Stripe account, its latest subscription, and prior trial usage. It returns
checkout availability, trial eligibility, period end and cancellation state.
These reads are user-specific and must not use a shared public cache.

Starting a trial invokes an authenticated server action. It commits the customer
mapping and exact session parameters before creating/reusing a hosted subscription
Checkout Session under the account lock. The server validates the configured
monthly USD 10 price and decides trial eligibility. The browser redirects to the
returned Stripe URL. Payment information goes directly to Stripe; there is no
client payment SDK. Checkout preserves language and the normalized search return
path. On completion, billing refreshes up to 15 times at two-second intervals
while awaiting verified state. Browser completion alone grants no paid access.

Manage subscription creates an authenticated Stripe Customer Portal session
using the configured portal policy and localized return URL. Expired customers
retain this action. `/:lang/checkout` redirects to billing settings; it accepts
no provider transaction parameter.

Stripe sends lifecycle events to `/api/stripe/webhook`. Signature verification,
account locking, current-state retrieval and durable processing finish before
acknowledgement. Historical Paddle webhook/deletion/entitlement handling remains
for existing records, as described in the runbook.
