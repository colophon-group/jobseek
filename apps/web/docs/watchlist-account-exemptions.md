# Watchlist ownership exemptions

Accounts normally own at most ten watchlists, on both Free and paid plans.
For operator-approved accounts, set the server-only environment variable
`WATCHLIST_LIMIT_EXEMPT_USER_IDS` to a comma-separated list of exact `user.id`
values. Whitespace around entries is ignored. Empty or absent configuration
grants no exceptions; emails, prefixes and wildcards are not supported.

An exempt account has no ownership ceiling. Both the page-data eligibility
check and the transaction-locked create/copy/handoff guard use this policy.
The result uses `max: null` to represent no ceiling. Paid features, billing,
alerts, rate limits, authorization and other accounts remain unchanged.

Resolve the requested account's stable ID from the production user table by
exact email, configure only the intended environment, and deploy through
`Deploy web production`. Vercel environment changes apply to new deployments.
Do not commit production IDs or credentials. Verify the intended account can
create and reload a watchlist above ten, and keep the ordinary-account tests
green. Removing its ID and redeploying restores the normal creation cap;
existing watchlists are preserved.
