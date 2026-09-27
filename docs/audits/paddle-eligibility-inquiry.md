# Paddle eligibility inquiry — owner reports sent

Issue: [#10078](https://github.com/colophon-group/jobseek/issues/10078)

To: sellers@paddle.com

Send from the email linked to the Paddle seller account. Paddle identifies this
address and account-email requirement in its [seller support guidance](https://www.paddle.com/help/start/intro-to-paddle/how-do-i-contact-support).

Subject: Product eligibility clarification — Job Seek Pro / Narrowed

---

Hello Paddle team,

Before submitting my website for verification, I would like to confirm whether
Job Seek Pro is eligible under your Acceptable Use Policy.

Job Seek (https://jseek.co) is a web application for job seekers. Users can browse
job postings, create company watchlists and track applications. We aggregate
postings from public employer career pages, display job descriptions, and link
to the original postings.

The intended paid product is Narrowed: users enter criteria such as “Python
backend roles without on-call duties,” and the software filters the job postings
in their watchlists against those criteria. It evaluates job descriptions rather
than scoring or selecting job applicants. Job browsing and standard filters
remain free. Employers are not charged for listings or promotion under this model.

Pro is US$10/month after a seven-day trial requiring a payment method. The
subscription is solely for access to Narrowed. I operate as an individual based
in Switzerland.

Your policy lists job boards under prohibited advertising services. Does that
restriction include this seeker-paid software model? If you can support it,
please confirm the scope and any conditions or evidence needed before I submit
verification.

The website's offer and policy updates are in preparation. The content-rights
assessment is also ongoing; this inquiry asks for product-category clarification
and is not an assertion that content-rights review is complete. I can provide a
demo and updated review links on request.

Thank you.

---

## Internal evidence and decision boundary

- Owner-confirmed price and scope: US$10/month, paid Narrowed filtering only.
- Current task branch refreshed to main `4966bcde0ec0be2018f7117242818181401c0979`.
- Billing PR #10073 inspected at `f0f59c6b39b5bb34572c26214258538597d143b6`.
  It specifies the trial, free features and offer. These remain proposed until
  merged/deployed and checked.
- The matching request in `apps/web/src/lib/ai-filter/jev-client.ts` contains
  normalized user criteria and job payloads, and classifies each job as matching
  or not matching. Its inputs do not implement applicant scoring. Users may
  include personal information in free-text criteria; do not make a blanket
  claim that no personal data is processed.
- [Paddle AUP](https://www.paddle.com/help/start/intro-to-paddle/what-am-i-not-allowed-to-sell-on-paddle)
  needs Paddle's interpretation for this offering. Its content-rights conditions
  remain a separate issue (#10079), even if the category is accepted.
- The owner confirmed on 2026-09-27 that the inquiry was sent. The text above
  is the prepared draft; the actual sent email and reply remain with the owner.
  Do not mark #10078 resolved until there is a written
  determination covering the actual product and any conditions are addressed.
- Keep the actual reply and any account-specific information private; record a
  non-sensitive result and date in the issue.
