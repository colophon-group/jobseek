# Privacy disclosure evidence — 27 September 2026

For [#10082](https://github.com/colophon-group/jobseek/issues/10082).
Only non-sensitive findings belong here. No credentials, resource IDs, host IPs,
customer rows, personal email destinations or provider-account screenshots are included.
Read-only production inspection was authorized by the owner.

## Verified configuration

| Item | Evidence | Finding |
| --- | --- | --- |
| Production release | Vercel project production target | `4966bcde0ec0be2018f7117242818181401c0979` |
| Application hosting | Vercel project region | `fra1`, Frankfurt, Germany; this is not a global data-residency guarantee |
| Web database | Production `DATABASE_URL`, inspected in memory with credentials suppressed | Supabase, `eu-central-1` (Frankfurt); old Neon wording was incorrect |
| Cache | Upstash account metadata | One Redis database; primary and member region `eu-central-1` |
| Search hosts | Hetzner Cloud location metadata | Nuremberg, Germany |
| Backup storage | Hetzner Storage Box API location metadata | Helsinki, Finland |
| Web backups | Read-only SSH, backup timer and allowlisted fields from status JSON | Timer active; latest reported success `2026-09-27T12:39:43.001919+00:00` |
| Backup retention | Deployed subsystem's repository runner/runbook | 30 daily, 12 weekly, 12 monthly snapshots; no hard 30-day erasure promise for backups |
| Checkout | Production environment metadata | `PADDLE_CHECKOUT_ENABLED=false`; no payment activation performed |
| Public support domain | DNS MX lookup | Cloudflare email routing; DNS alone does not prove end-to-end delivery or mailbox monitoring |

This is a point-in-time inspection, not continuous monitoring. No production
configuration, database records or provider contracts were modified.

## Code-supported data flows

- `apps/web/src/lib/auth.ts`: email/password plus Google, GitHub and LinkedIn;
  installed Better Auth password module uses password hashing. Account/session
  records include technical metadata. The `logged_in` cookie persists beyond
  a browser session.
- `apps/web/src/db/schema.ts`: watchlists, sharing, application tracking,
  notification delivery, Narrowed configuration/decisions/usage. Linked account
  records cascade on account deletion. Published/unlisted watchlists expose
  information according to their sharing setting.
- `apps/web/src/lib/services/query-intent.ts`: search text and locale go to
  TypeSafe. This flow exists separately from paid Narrowed.
- `apps/web/src/lib/ai-filter/classifier-input.ts` and `jev-client.ts`: normalized
  criteria and job ID/title/company/description text go to TypeSafe. No account
  email, credentials or application notes are selected into that request, but
  user-entered text can itself contain personal information.
- `apps/web/src/lib/email.ts` and notification delivery service: Resend receives
  recipients and message content. The recently merged notifications change is
  in the inspected production release.
- `apps/web/src/lib/rate-limit.ts` and cache layer: Upstash handles counters
  keyed by IP/account and cached data; public delivery/search also uses Cloudflare.
- Billing data flow is still the proposed integration in
  [#10073](https://github.com/colophon-group/jobseek/pull/10073), inspected at
  `c321a9107c12faafb967cc8ed0153a17cf604c9f`: account email/reference to Paddle;
  subscription/customer IDs, status, billing dates and trial usage back to Job Seek.

## Provider sources checked

- [TypeSafe privacy](https://typesafe.ai/legal/privacy-policy): US hosting,
  no training/fine-tuning on inputs, purpose-based retention rather than a fixed
  standard API deletion window. This is a provider statement, not an independently
  audited guarantee or evidence of a zero-retention account setting.
- [TypeSafe MCA](https://typesafe.ai/legal/mca) incorporates its data-processing
  agreement; [DPA](https://typesafe.ai/legal/data-processing) describes processor
  roles and transfer clauses, including Swiss treatment. Account-specific
  amendments cannot be established from public documents alone.
- [Resend GDPR documentation](https://resend.com/security/gdpr): US storage,
  standard 30-day email/log retention, separate backup/termination windows,
  DPA effective on sign-up and SCC provisions with Swiss adaptations.
- [Vercel DPA](https://vercel.com/legal/dpa),
  [Supabase DPA](https://supabase.com/legal/customer-resources/data-processing-addendum),
  [Paddle privacy](https://www.paddle.com/legal/privacy): use the applicable roles
  and provider notices; do not label Paddle merely a Job Seek processor.
- [FDPIC transparency guidance](https://www.edoeb.admin.ch/en/duty-to-provide-information)
  is the basis for identifying the Swiss controller, purposes, recipients and
  international processing in accessible language.

## Remaining acceptance work

Publish/recheck the reviewed notice after reconciling the billing PR, retain the
applicable provider agreements privately, and verify the operational support and
data-rights workflow. Public standard terms are useful evidence, but this task
does not certify the owner's contract history, all provider subprocessors, or
actual response handling. Do not promise instantaneous deletion of provider
records or historical backups. Preserve deletion requests across any recovery
procedure rather than interpreting restoration as permission to reactivate an
account; evaluate that procedure in the backup runbook before relying on it.

This bounded disclosure work does not reopen the deferred general AI-review
umbrella #8328 or make zero retention a newly invented Paddle prerequisite.
