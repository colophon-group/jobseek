# Paddle domain-review packet

Updated 27 September 2026. Tracker [#10084](https://github.com/colophon-group/jobseek/issues/10084).
Preparation evidence; no application, identity submission or live charge was
made by this task. The owner already sent the separate category inquiry.

## Current owner decisions (supersede the earlier publication snapshot)

The full Terms and Privacy documents are published on GitHub and linked from
translated summaries on the site. The owner requested removal of the embedded
full English documents; seller/contact information, payment/refund summaries and
footer links remain on-site. The earlier full-text rendering checks below record
historical publication, not the current presentation. GitHub-only full-document
hosting has not been individually approved by Paddle; no categorical prohibition
or acceptance was established by its public guidance.

The owner also chose to retain descriptions as-is and declined a per-source
rights register or description-transformation requirement. These are settled
product decisions, not a blanket finding of rights clearance. Scoped TDM
reservation handling remains engineering follow-up under #10090; it is not an
express Paddle application checklist item. The current decision and deployment
status are maintained in #10084 and supersede the older readiness tally below.

## Seller and offer

- Viktor Shcherbakov, individual operator in Switzerland; product **Job Seek**.
- Public contact: Route d'Oron 5, 1010 Lausanne, Switzerland;
  business@colophon-group.org. The owner confirmed on 27 September that the
  inbox is working; operational support confirmation is complete.
- **US$10/month for Narrowed filtering only**, plus applicable tax. Eligible new
  subscribers get seven days with a payment method required, followed by monthly
  renewal unless cancelled. Returning subscribers have no repeat trial.
- Standard search, up to ten watchlists, email alerts and application tracking
  remain free. No employer-paid listing/advertising product is offered.
- Billing #10073 merged at `52e8c7b6af58c777d4895e31753331b9d44eac75`.
  Owner reports migration applied, live webhook activated/verified, CI and
  deployment passed. These customer-flow actions were performed outside this task.

## Production publication verified

Policy PR [#10087](https://github.com/colophon-group/jobseek/pull/10087) merged at
`334f5631d61e7f48eb0b52dad6fdc2c2bc2f68e3`; deployment
[36326666570](https://github.com/colophon-group/jobseek/actions/runs/36326666570)
completed successfully on 27 September 2026.

| Surface | Public URL | Verified evidence |
| --- | --- | --- |
| Offer | https://jseek.co/en#pricing | Narrowed-only, US$10/month after seven days; ten free watchlists and free alerts/tracking |
| Terms | https://jseek.co/en/terms | Complete canonical text, proprietor/address, Swiss law with mandatory consumer rights, billing/cancellation terms |
| Refunds | https://jseek.co/en/terms#refund-policy | Public policy and working localized footer anchor; Paddle support/receipt request paths |
| Privacy | https://jseek.co/en/privacy-policy | Complete canonical notice, actual data flows/providers/retention, Swiss controller |
| Checkout host | `jseek.co` | Planned payment page https://jseek.co/en/checkout; checkout remains disabled pending approval |

Anonymous Chromium checks passed for Terms and Privacy in **en/de/fr/it**.
Rendered complete documents exactly matched repository files after whitespace
normalization. All routes returned HTTP 200; seller address and refund links
were present. Desktop and 390px mobile checks found no overflow or browser errors.
Full canonical documents are English and labelled; navigation and summaries
are translated. [Machine-readable results](paddle-evidence/policy-smoke-results.json).

All four live offer pages were also read and captured:
[English](paddle-evidence/offer-en.png), [German](paddle-evidence/offer-de.png),
[French](paddle-evidence/offer-fr.png), [Italian](paddle-evidence/offer-it.png).
[Offer text](paddle-evidence/offer-evidence.json) records the observed copy.
The pictured matching example is marketing illustration, not a production test.

## Live Paddle/configuration observations

Read-only checks on 27 September:

- Onboarding `in_progress`; domain, business-identification, identity and final
  review returned `pending`. A generic business-identification status does not
  override Paddle's sole-trader exemption.
- No checkout domain returned for `jseek.co`; no approval established.
- Active monthly USD 10 prices exist for the seven-day payment-method-required
  trial and the returning-subscriber no-trial offer; tax mode is location-based.
- An active platform webhook targets `https://jseek.co/api/paddle/webhook` with
  the expected subscription lifecycle events. Its presence is not category approval.
- Vercel production configuration reports `PADDLE_ENVIRONMENT=production` and
  `PADDLE_CHECKOUT_ENABLED=false`. Narrowed switches are configured as sensitive
  and redacted by the CLI; their values were not established by that read.

[Paddle domain guidance](https://www.paddle.com/help/start/account-verification/what-is-domain-verification)
permits pricing screenshots. A successful live charge, dedicated /pricing route
or search-engine indexing is not a stated domain-review prerequisite. If Paddle
requests product access, supply a private entitled account or a recorded real
Narrowed demonstration; confirm production flags/entitlements at that point.
Never put account credentials in this repository.

## Remaining pre-submission work: two groups

1. **Product eligibility — Paddle/owner, #10078.** Await the written decision on
   seeker-paid filtering in view of the job-board advertising restriction.
   The owner sent the inquiry; no reply has been supplied. This is the audit's
   recommended category gate, not a claim that Paddle requires a legal-opinion form.
2. **Content-use decision and controls — owner/Codex, #10079 and #10090.** The
   assessment is complete, but blanket fair use does not establish every source
   use. Decide source-dependent full text versus factual summaries/links, and
   document the collection/display/AI grounds. Follow-up repairs cover Ashby and
   Greenhouse API reservations and HTML parsing; complete active-path coverage,
   origin-file signals and restrictions on further use of affected stored content
   remain open. No production-wide exclusions were applied without the source
   policy decision. Full removal implementation is expressly deferred below.

These are readiness judgments, not additional forms imposed by Paddle.
Paddle's actual domain/identity/final review still occurs during its application
process. The product access evidence noted above is a conditional handoff, not
an invented universal prerequisite for submitting the website.

## Other remaining or deferred work (not counted twice)

- **Swiss administration, #10080:** owner has not yet raised the activity with
  Swiss authorities. Privately determine AVS/AHV self-employment recognition and
  applicable registration/tax duties. Paddle
  [exempts sole traders from business verification](https://www.paddle.com/help/start/account-verification/what-is-account-verification);
  that does not settle Swiss administration. Use the
  [Swiss SME guide](https://www.kmu.admin.ch/en/legal-form-sole-proprietorships),
  without inventing a UID, VAT number, certificate or incorporation requirement.
  Public identity/Terms publication is complete. Refund-policy issue #10081
  is complete with the owner's support-inbox confirmation.
- **Removal procedure:** owner expressly chose implementation upon the first
  request. Not a pre-verification blocker. Existing dataset scrub support covers
  dated JSONLs only; no tested cross-system takedown guarantee is claimed.
- **Dataset retention, #10091:** both Job Seek datasets are private. Scoped notices
  distinguish owned contributions from source text; upload guards fail closed
  on non-private visibility. See [retention register](dataset-retention-and-removal.md).
  Remaining source-use grounds are counted under #10079; historical removal is
  part of the deferred first-request work.
- **Robots, #2841:** technical enforcement remains tracked internally. Public
  robots.txt/Disallow implementation details are removed at the owner's request.
- **Live billing launch:** after approval, retain real entitlement, cancellation,
  account-deletion and refund-delivery evidence and explicitly activate checkout.
  Policy-page tests do not establish successful customer payment flows.
- **Unrelated Hugging Face quota:** nine of ten personal datasets were made
  private. WildChat-1M-sampled-for-message-classification could not be made private
  because the account's private storage limit would be exceeded. This is separate
  from Job Seek's verification readiness; no purchase or deletion was authorized.
