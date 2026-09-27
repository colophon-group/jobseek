# Paddle verification readiness — 27 September 2026

Jobseek is **not yet ready to submit**. A Swiss individual/sole trader can use
Paddle's onboarding route, but that does not establish product eligibility.
The owner confirmed the intended purchase: US$10/month for Narrowed filtering
of job postings on the website.

The actionable record is [tracker #10084](https://github.com/colophon-group/jobseek/issues/10084).

| Issue | Work before submission |
| --- | --- |
| [#10078](https://github.com/colophon-group/jobseek/issues/10078) | Obtain Paddle's product-specific eligibility decision |
| [#10079](https://github.com/colophon-group/jobseek/issues/10079) | Establish the rights basis for third-party job content |
| [#10080](https://github.com/colophon-group/jobseek/issues/10080) | Confirm Swiss seller details and publish complete Terms |
| [#10081](https://github.com/colophon-group/jobseek/issues/10081) | Publish refund, cancellation and trial policy |
| [#10082](https://github.com/colophon-group/jobseek/issues/10082) | Update privacy disclosures |
| [#10083](https://github.com/colophon-group/jobseek/issues/10083) | Publish the final offer and prepare the domain-review packet |

## Publication follow-up (same day)

Policies and the Narrowed offer are now published. #10087 merged as
`334f5631d61e7f48eb0b52dad6fdc2c2bc2f68e3`, and production deployment
[36326666570](https://github.com/colophon-group/jobseek/actions/runs/36326666570)
passed. All eight Terms/Privacy locale routes were checked anonymously in
Chromium: full text matches the canonical files, the Swiss address is present,
refund links work, and desktop/mobile checks pass. #10082 is closed.

The earlier findings below are historical. For current status and the remaining
blocker tally use the [domain packet](paddle-domain-review-packet.md). The owner
has deferred complete cross-system takedown implementation until the first
request; that work is not a pre-verification condition. Robots implementation
details are being removed from public indexing copy at the owner's request.

## Scope and evidence

- Worktree: `/Users/Viktor/.codex/worktrees/paddle-verification-readiness/jobseek`.
- Branch: `codex/paddle-verification-readiness`.
- Base: freshly fetched `origin/main`, `cb6d94bbf6aecb398616ff896923f082268a2fce`.
- Read the repository Terms, Privacy Policy, public policy/pricing components,
  billing stubs, entitlement limits, authentication and matching-provider code.
- Reviewed [PR #9848](https://github.com/colophon-group/jobseek/pull/9848),
  [#9881](https://github.com/colophon-group/jobseek/pull/9881), and the open draft
  [#10073](https://github.com/colophon-group/jobseek/pull/10073). The latter was
  inspected at `8aed929a27619144c162c908cafd2eb5fae31ac1` and already owns Paddle
  billing, a seven-day payment-method-required trial, and the revised Narrowed
  offer. [#10075](https://github.com/colophon-group/jobseek/pull/10075) separately
  owns email delivery. No duplicate integration issue was created.
- Direct HTTPS GETs returned 200 for `/en`, `/en/terms`,
  `/en/privacy-policy`, and Terms in German, French and Italian. The homepage
  still advertised one Free watchlist and unlimited Pro watchlists; current
  main caps every account at ten. `/en/refund-policy` returned 404, and no refund
  link appears in the footer configuration or returned homepage markup.
- Terms and Privacy are accessible summaries linking to complete documents in
  the public GitHub repository. They are not missing pages. Full Terms specify
  German law/Berlin courts, generic paid-feature wording and a non-refundable
  default. Privacy describes an older OAuth/hosting/storage model.
- A read-only live Paddle API check returned onboarding `in_progress`, account
  setup `in_progress`, and domain review, business identification, identity
  verification and final review `pending`. The checkout-domain query for
  `jseek.co` returned zero entries, with no further page.

## Decisions

Paddle's [AUP](https://www.paddle.com/help/start/intro-to-paddle/what-am-i-not-allowed-to-sell-on-paddle)
lists job boards under prohibited advertising. The seeker-paid filtering model
needs an explicit determination; changing the label to SaaS is not evidence of
acceptance. No prior written determination was supplied. The eligibility and
rights issues are readiness recommendations based on this exposure, not claims
that Paddle mandates a separate legal-opinion form.

The content-rights basis remains unresolved. A general fair-use assertion does
not demonstrate permission or an applicable exception for every commercial use.
This audit did not determine whether any particular posting is protected or
infringing. See the Swiss IPI's [using a work](https://www.ige.ch/en/protecting-your-ip/copyright/using-a-work)
and [permitted uses](https://www.ige.ch/en/protecting-your-ip/copyright/using-a-work/permitted-uses)
guidance.

[Paddle's verification guidance](https://www.paddle.com/help/start/account-verification/what-is-account-verification)
exempts individuals and sole traders from business verification; individual
[identity verification](https://www.paddle.com/help/start/account-verification/what-is-identity-verification)
still applies. Swiss commercial-register and social-insurance obligations are
separate, as described by the [Swiss SME portal](https://www.kmu.admin.ch/en/legal-form-sole-proprietorships).
Seller name, contact address and applicable administrative steps need owner
confirmation. No corporation or invented registration number is required by
this audit.

The policy and publication issues use Paddle's [domain-review criteria](https://www.paddle.com/help/start/account-verification/what-is-domain-verification),
[refund policy](https://www.paddle.com/legal/refund-policy),
[Swiss e-commerce guidance](https://www.kmu.admin.ch/en/statutory-obligations-swiss-and-european-e-commerce-laws)
and [FDPIC transparency guidance](https://www.edoeb.admin.ch/en/duty-to-provide-information).
Refund wording must preserve mandatory rights without inventing a universal
money-back promise. The privacy task is bounded disclosure work, not a reopening
of the deferred broad AI legal-review issue #8328.

## Limits and next step

Resolve eligibility first, then complete the linked review packet. Full payment
activation is a separate launch gate in PR #10073; its reported sandbox and local
webhook tests do not prove deployed public webhook delivery. No app tests were
run for this documentary audit. No code, production environment, payment state,
Paddle submission, support correspondence or government registration was changed.
The primary checkout and its uncommitted work were left untouched.

## Resolution work started — same-day follow-up

The observations above describe the original audit, not the current completion
status. The owner has since supplied the public Swiss address, selected a
focused content-rights review, and reported that the eligibility inquiry was sent.
The task branch was refreshed to main `4966bcde0ec0be2018f7117242818181401c0979`
before implementation; the primary checkout remains untouched.

Proposed changes now include complete on-site Terms/Privacy, the Swiss seller
and controller address, corrected provider/authentication/backup disclosures,
a linked refund section aligned with #10073, and a data-licence scope that
excludes third-party rights. Full policies are read from the canonical repository
files by a Server Component; all four locale pages identify the English text.
No legal conclusion about source content follows from these wording corrections.

See the [rights-review brief](content-rights-review-brief.md),
[production privacy evidence](production-privacy-evidence.md), and
[domain-review packet and decision register](paddle-domain-review-packet.md).
The issues remain open until their acceptance evidence exists, including
publication, Paddle's decision, and content-rights remediation.

At the owner's subsequent direction, Codex conducted the review itself. The
[completed content-rights assessment](content-rights-assessment.md) distinguishes
the defensible conditional filtering model from unsupported blanket full-text
reuse, records live public-dataset evidence and reproduces TDM enforcement gaps.
No external reviewer is required by this task. #10079 remains open for the
documented source decisions, implementation and operational evidence.
