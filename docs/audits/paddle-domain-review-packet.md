# Paddle domain-review preparation packet

Prepared 27 September 2026. Tracks
[#10083](https://github.com/colophon-group/jobseek/issues/10083) under
[#10084](https://github.com/colophon-group/jobseek/issues/10084).
**Preparation only: do not submit this as an approved or complete application.**

## Seller and product

- Supplier/operator: Viktor Shcherbakov, an individual based in Switzerland.
- Product brand: Job Seek. Public address confirmed by the owner:
  Route d'Oron 5, 1010 Lausanne, Switzerland.
- Product/support contact: business@colophon-group.org.
- Paid feature: Pro provides Narrowed filtering of job postings against a
  job seeker's criteria. It does not rank candidates or provide employer-paid
  listings. Price: US$10/month; proposed billing PR includes a seven-day
  payment-method-required trial for eligible first-time subscribers, automatic
  monthly renewal, and cancellation before the first charge.
- Standard search, up to ten watchlists, email alerts and application tracking
  are intended to remain free. Offer deployment is owned by
  [#10073](https://github.com/colophon-group/jobseek/pull/10073).

## Website evidence to capture after merge/deployment

| Surface | Intended URL | Evidence still needed |
| --- | --- | --- |
| Offer | https://jseek.co/en | Final Narrowed-only offer; no old one/unlimited-watchlist or paid-email copy; price/trial/tax/renewal agreement across locales |
| Terms | https://jseek.co/en/terms | Complete text on-site, seller address, current date and no sign-in dependency |
| Refunds | https://jseek.co/en/terms#refund-policy | Visible policy linked from footer/offer and working Paddle support paths; a separate route is unnecessary |
| Privacy | https://jseek.co/en/privacy-policy | Complete current notice, actual providers/countries and backup retention |
| Checkout | https://jseek.co/en/checkout | Planned payment page from #10073; checkout remains disabled until separately approved for launch |
| Product demonstration | Authenticated watchlist Narrowed flow | Real reviewer-accessible feature, available entitlements/flags and representative matching results; no credentials in this repository |

Repeat the public checks for `/de`, `/fr` and `/it`. Full canonical documents
are English and explicitly labelled; summaries and navigation are localized.
Record deployed commit, UTC check time, screenshots and anonymous HTTPS results.
Use private test-access handoff if Paddle requests it. A pricing screenshot is
allowed by [Paddle domain guidance](https://www.paddle.com/help/start/account-verification/what-is-domain-verification);
a functional live charge is not required to prepare a domain review.

Checkout host inventory currently contains only `jseek.co`; add any additional
hosts if actually used. Local/preview smoke checks are not production evidence.
Public support MX records exist, but the owner still needs a working, monitored
support inbox; DNS is not proof that a customer can get a response.

## Decision register

| Issue | Status / next acceptance evidence |
| --- | --- |
| #10078 — eligibility | Owner reports inquiry sent on 27 September. Await written Paddle response and satisfy any conditions. |
| #10079 — content rights | Owner selected a focused legal review of the current model. Brief: `content-rights-review-brief.md`. Assessment and resulting changes remain open. |
| #10080 — seller/Terms | Public name/address and revised Terms prepared. Publication and owner's applicable Swiss administrative steps remain open. |
| #10081 — refunds | Wording and footer section prepared, aligned with #10073. Verify deployed links and implemented cancellation/deletion behavior after integration. |
| #10082 — privacy | Production evidence recorded in `production-privacy-evidence.md`; revised notice prepared. Reconcile/publicly verify on deployment and retain applicable agreements privately. |
| #10083 — domain packet | This packet is prepared. Final offer deployment, screenshots and actual reviewer-access demonstration remain open. |

Do not close issues solely because a draft document or pull request exists.

## Swiss individual-business preparation

Paddle's [account-verification guidance](https://www.paddle.com/help/start/account-verification/what-is-account-verification)
says sole traders do not undergo its business-verification stage; this does not
settle Swiss administrative obligations. The owner reports no Swiss authority
filing yet. Use the [Swiss sole-proprietorship guidance](https://www.kmu.admin.ch/en/legal-form-sole-proprietorships)
to determine the actual AVS/AHV self-employment recognition, commercial-register
and tax/VAT steps for the activity and turnover. Record the decision privately;
absence of a registry entry is not by itself an instruction to incorporate.
Do not invent a UID, VAT registration, company certificate or registration date.

Enter any requested identity/address/bank evidence through Paddle's own process.
Keep documents, date of birth, account references and private correspondence out
of this public repository. The public contact address is not proof that any
particular identity document has been accepted.

## Submission versus launch

Resolve the linked readiness decisions and obtain the owner's instruction to
start verification before submitting. The category inquiry already sent is
separate from formal verification. For later live billing, follow #10073's
migration/webhook/entitlement/cancellation/account-deletion checks and activation
runbook. In particular, reconcile migration numbering against the merged
notification migration. This preparation task has performed no deployment, migration, Paddle
submission, charge, refund, government filing or live-checkout activation.

## Local implementation validation

- Production build and TypeScript completed successfully in the isolated worktree
  without production service credentials. Optional Redis/auth warnings on the
  first build were environment configuration, not legal-page failures; the final
  build used a local-only auth secret and URL.
- Targeted ESLint and repository i18n coverage/compiled-catalog checks passed.
- Anonymous Chromium checks against the standalone production package returned
  HTTP 200 for Terms and Privacy in en/de/fr/it. Rendered complete-document text
  matched the canonical files exactly after whitespace normalization.
- Localized footer links reached the refund section. Desktop and 390px mobile
  checks found no horizontal overflow; no browser page errors were recorded.
- Next.js standalone tracing included both canonical documents. No production
  database, checkout, cancellation, refund or message-delivery flow was exercised
  by these local legal-page checks.
