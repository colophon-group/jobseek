# Billing settings (`/:lang/settings/billing`)

The `(app)` layout supplies the shared application shell. The billing page
loads authenticated plan information on the server. See
[the Paddle runbook](../paddle.md) for configuration and rollout.

`getPlanInfo()` reads the shared paid-entitlement rule, the environment-specific
Paddle account, and its latest subscription. It returns checkout availability,
trial eligibility, period end, and scheduled cancellation state. These reads
are user-specific and must not be placed in a shared public cache.

## User interactions

- Starting a trial invokes an authenticated server action. It creates or reuses
  a Paddle transaction while holding the account row lock. The server selects
  the trial or returning-customer price.
- Paddle.js is lazy-loaded only when checkout is needed. It opens an overlay
  using that transaction ID. Payment details go to Paddle.
- Checkout completion returns to billing settings, which refreshes up to 15
  times at two-second intervals while waiting for verified webhook state.
  Browser completion alone never grants AI-filter access.
- Manage subscription creates an authenticated Paddle portal session and
  navigates to its returned URL. Expired/canceled customers retain this action.
- `/:lang/checkout?_ptxn=...` initializes Paddle.js for public payment links.
  Paddle.js automatically opens the referenced transaction.

Paddle sends lifecycle events to `/api/paddle/webhook`. Signature verification
and durable database processing finish before acknowledgement. This is a
separate server request from the browser checkout.
