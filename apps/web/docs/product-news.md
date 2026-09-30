# Optional product news

Product news is an independent, explicit opt-in for occasional Job Seek feature
updates and offers. Existing users remain opted out. Account registration,
payments, cookies, job-alert opt-ins and banner clicks never imply consent.
There is no waitlist campaign or email send in this change.

Email registration has an optional unchecked checkbox. Better Auth records only
the boolean `true` with the current consent wording version on a newly created
email account. Social registrations stay opted out and can choose in Settings.
If the post-creation consent write fails, account creation stays usable and no
consent is inferred; a generic warning contains no personal data.

Settings has a separate Product news checkbox. Each real change appends an event
to `product_news_consent`, with the account, normalized address, language,
localized wording, wording version, source and timestamp. A row lock on the
account serializes first writes, concurrent changes and unsubscribe requests.
The latest sequence defines the preference; no event means opted out. History
is deleted with the account. Changing the account email requires a new opt-in
for that address. Email verification is also required before sending.

The gold Pro banner uses the established crown icon and the cookie-banner layout,
including the mobile position above navigation. It waits for the cookie banner,
appears for signed-in free users, and offers links to Narrowed and the Product
news preference. Dismissal persists locally per account and in account preferences;
neither dismissal nor following a link changes marketing consent.

## Deployment

Apply `0099_product_news_consent` through the reviewed routine migration workflow
before deploying code that reads this table. Its prerequisite is
`0098_stripe_billing`; the exact identity is in `drizzle/routine-migrations.json`.
The migration creates an empty, server-only table and revokes table and sequence
permissions from browser roles, even when default grants exist. It never
backfills existing users.

Configure `PRODUCT_NEWS_UNSUBSCRIBE_SECRET` with a dedicated random secret of at
least 32 characters before sending a campaign. It is included in both Turbo
build allowlists. Without it, unsubscribe tokens fail closed and no campaign
should be sent. Use protected environments; never commit secrets.

## Future campaigns

`getProductNewsRecipient(userId)` is a server-only eligibility lookup: it returns
only the latest opted-in choice for the account's current verified address.
Use it immediately before submission, and enforce Resend's bounce, complaint and
unsubscribe suppressions too. This change does not implement a bulk sender or
authorize importing every account into Resend Broadcasts. Exports can become
stale and must not be treated as ongoing permission.

Build a signed token with `createProductNewsUnsubscribeToken(recipient.id,
recipient.email, secret)` and include both a visible link and RFC 8058 headers:

```text
List-Unsubscribe: <https://jseek.co/api/product-news/unsubscribe?token=TOKEN>
List-Unsubscribe-Post: List-Unsubscribe=One-Click
```

GET only renders a localized confirmation; POST with
`List-Unsubscribe=One-Click` appends withdrawal without requiring login. Neither
path changes watchlist notifications or account emails. Tokens are bound to the
address and consent event, domain-separated from job-alert tokens, and pages
disable caching, referrers and indexing. Repeated POSTs are idempotent. An old
email cannot withdraw a later affirmative re-opt-in: its page directs the person
to manage the current choice in Settings. Database failures return 503 for retry.
If a future campaign uses Resend's own unsubscribe links, synchronize those
withdrawals to this ledger as part of that sender's implementation.

Increment `PRODUCT_NEWS_CONSENT_VERSION` when changing the scope or wording, and
keep `PRODUCT_NEWS_CONSENT_TEXT` synchronized with all four UI catalogs. Older
forms can still withdraw consent; only affirmative choices require the current
version.

## Verification

Focused component/action/token/route tests cover checkbox defaults, explicit
choices, authorization, cookie-banner priority and one-click behavior. Execute
the real migration, Better Auth signup, concurrent writes, address/verification
eligibility, withdrawal, dismissal persistence, browser isolation and deletion
against a named disposable local PostgreSQL database:

```sh
PRODUCT_NEWS_TEST_DATABASE_URL=postgresql://USER@127.0.0.1:5432/jobseek_product_news_fixture \
  pnpm exec vitest run src/lib/services/__tests__/product-news-pg.test.ts
```

This test creates and truncates fixture tables. It refuses databases without
`product_news_fixture` in the name or with a non-local host. CI runs it against
PostgreSQL 17 in its own database.
