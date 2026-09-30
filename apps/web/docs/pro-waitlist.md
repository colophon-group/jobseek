# Pro waiting list

When `stripeCheckoutEnabled()` is false, the homepage pricing section, Narrowed
page, and billing settings show the same email signup form. Visitors do not need
an account. Signed-in visitors can use their account email or enter another one.
When checkout opens, the existing trial and subscription flow takes its place.

`joinProWaitlist` stores a normalized email, locale, and signup timestamp in
`pro_waitlist`. The email is the primary key; repeated signups succeed without
overwriting the original locale or timestamp. The action does not disclose
whether an address already exists. It uses the existing Redis service to limit
each IP to five attempts per hour, and returns a retryable error if Redis or the
database is unavailable. RLS and revoked browser-role grants keep the table
accessible only to the server.

Apply `0097_pro_waitlist` through the routine web database migration workflow
before deploying this feature. Its identity is registered in
`drizzle/routine-migrations.json`; its prerequisite is `0096_paddle_billing`.
No new environment variables or services are required.

This feature records consent for a Pro launch email. It does not send emails or
create a subscription. Launch outreach can read `email`, `locale`, and
`created_at` from `pro_waitlist`, ordered by `created_at`; sending those emails
is a separate operation. Removal requests can delete the normalized email from
this table without affecting any Job Seek account.
