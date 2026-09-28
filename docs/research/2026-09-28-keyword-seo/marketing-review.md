# Marketing page review

28 September 2026 · Follow-up to the [50-persona keyword analysis](report.md)

## Decision

**Keep the company-first positioning. Correct product promises first, make the first action more specific, and add a small number of pages for distinct customer tasks.** The research supports this direction as a hypothesis; it does not prove that these changes will increase traffic or conversion.

The homepage already leads with company watchlists and renders the watchlist feature before application tracking and search. A wholesale repositioning is unnecessary. The larger gaps are accuracy, evidence, and explaining how to start.

## Scope and evidence

Reviewed the current repository at `123a3e928`, including homepage metadata, Hero, Features, Pricing, ProPitch, About, FAQ, blog index/articles, all four translation catalogs, notification policy, saved-job creation, and company requests. Read the production pages and inspected browser-rendered English home/About/FAQ/blog and German/French/Italian homepages. Checked the homepage at 1440px and 390px and followed the public Pro CTA.

The 390px homepage had no horizontal document overflow. This is a focused marketing/content review, not an accessibility audit or conversion experiment. Some web-tool About/blog responses were stale; current source and direct browser responses take precedence. No marketing implementation was changed. Production email activation and actual delivery were not audited.

## Fix first: promises that exceed the verified behavior

| Priority | Current observation | Recommended change | Evidence |
|---|---|---|---|
| P0 | About and FAQ promise notification the moment a role appears. | Say **weekly email digest**, distinguish it from the freshness of the in-app feed, and explain opt-in. Verify production activation before actively advertising delivery. | [Notification documentation](../../../apps/web/docs/notifications.md), [weekly cadence policy](../../../apps/web/src/lib/notifications/policy.ts), [About](<../../../apps/web/app/[lang]/(public)/about/about-content.tsx>), [FAQ](<../../../apps/web/app/[lang]/(public)/faq/page.tsx>) |
| P0 | Hero, features, About and FAQ imply listings arrive before LinkedIn/Indeed; homepage metadata promises no reposted listings, and FAQ promises no reposted ghost jobs. | Retain the factual claim that listings are sourced from company career pages. Remove comparative speed and hiring-intent guarantees unless supported by a reproducible study. A company's own posting can still be stale or not lead to a hire. | [Hero](../../../apps/web/src/components/Hero.tsx), [Features](../../../apps/web/src/components/Features.tsx), [homepage metadata/JSON-LD](<../../../apps/web/app/[lang]/(public)/page.tsx>), [live FAQ](https://jseek.co/en/faq) |
| P0 | The hero promises to track every application; feature copy says users can save any role they find. | Specify **jobs saved on Job Seek**. Do not imply imports from other websites or arbitrary manual application entry. | [Saved-job action](../../../apps/web/src/lib/actions/saved-jobs.ts) requires an indexed posting ID and fetches its snapshot before insertion. |
| P0 | The homepage advertises a seven-day Pro trial, while the signed-out billing destination says trial signup is not open yet. | State current availability beside the homepage offer and use an informational CTA until checkout is actually enabled. Recheck availability before shipping copy. | Direct browser visit to [the public billing page](https://jseek.co/en/settings/billing); the page remained accessible without signing in. |
| P1 | Request-company copy says pasting a URL means indexing starts. | Explain that users submit a request and can follow its progress. Avoid guaranteed coverage or a time-to-add claim. | [Request action](../../../apps/web/src/lib/actions/request-company.ts) records a request and attempts to create a GitHub issue; successful submission is not successful indexing. |
| P1 | Search is described as providing every filter a seeker needs. | Name the supported dimensions. Include employment type and work mode alongside role, location, salary and posting language; avoid implying that shifts, sponsorship or travel are standard filters. | [Features](../../../apps/web/src/components/Features.tsx), [search filter implementation](../../../apps/web/src/lib/search/typesense-filters.ts), persona constraint examples. |

The notification mismatch changes the interpretation of the keyword research: company monitoring still fits, but **instant-alert intent is not currently satisfied by the weekly email policy**. Do not optimize pages around instant notifications without implementing and verifying that capability. Cadence policy is not proof that mail is live: runtime defaults to off and the repository documents a staged activation process.

Apply corrections to English, German, French and Italian together, including metadata and FAQ JSON-LD. The translated hero paragraphs repeat the comparative speed claim; the German FAQ also implies immediate notification.

## Homepage: make the existing proposition easier to act on

### Proposed hero copy

**Heading:** Follow the companies you want to work for.

**Body:** Find jobs from company career pages, save the employers and filters that matter to you, and track the roles you save on Job Seek.

**Primary action:** Find companies to follow

**Secondary action:** See how watchlists work

**Supporting line:** Free search, up to 10 watchlists, and application tracking.

Once production email activation is confirmed, add: **Opt in to a weekly email digest of new matches.** The line should not promise an email every week when there are no new matches.

The current primary button goes to Explore, so “Find companies to follow” matches the destination better than “Create an alert” would. “Create my first watchlist” becomes appropriate if the button actually opens that flow, preserves company choices through sign-in, and handles missing employers. The secondary can keep linking to the existing feature section, with its new label matching the content.

Keep the company-first headline, but soften the eyebrow that restricts the audience to people who *already* know their employers. Suggested wording: **A focused job search, built around your employers and preferences.** Provide a subordinate route for “Still choosing companies? Search by role and location.” Do not give two competing primary buttons equal emphasis.

Show a short three-step sequence near the first product screenshot:

1. Find companies and set role/location filters.
2. Save a watchlist and optionally enable its weekly digest.
3. Save relevant jobs and track applications and interviews.

The second step is conditional on verified email availability. A labeled sample digest would demonstrate the alert promise more directly than another abstract benefit sentence.

### Visual hierarchy

The existing typography and artwork are distinctive; preserve them. On mobile, substantial illustration and feature blocks separate the initial action from pricing and the free-plan explanation. Move the free-plan reassurance beside the first CTA and add a contextual CTA after the watchlist demonstration. Consider moving a compact product demonstration higher while keeping the artwork as supporting identity.

These are layout hypotheses, not measured conversion defects. The existing feature order—watchlists, tracker, search—is reasonable. Test a shorter path to creating a useful watchlist before changing the whole visual design.

## Pro and pricing: keep the concrete demonstration, broaden the relevance

The [current Pro section](../../../apps/web/src/components/pro/ProPitch.tsx) already does several things well: it labels the fictional example, shows two explicit posting excerpts, states the trial and monthly price, and distinguishes free features from Narrowed results.

Add a compact non-software example after checking it against actual classifier behavior: for example, a role requiring a permanent contract or explicitly limited travel. This makes the feature relevant to more of the researched audience without asserting coverage for a particular city or profession.

Explain that Narrowed assesses the information in postings, rather than verifying employment conditions with employers. If a posting omits a requirement, the page should not imply that the condition is satisfied. The [classifier instruction](../../../apps/web/src/lib/ai-filter/jev-client.ts) accepts only jobs that clearly satisfy preferences, and otherwise rejects them. Do not invent a three-state “unknown” UI merely for the marketing illustration; any such state would be a separate product change.

“Explore Pro” currently points to `/settings/billing`. The direct browser check confirmed that this page is accessible while signed out and contains the Pro explanation, price, and **“Trial signup isn’t open yet.”** The problem is offer availability, not a broken link or forced login. Put that availability next to the homepage offer too; “Learn about Pro” would set the current expectation better. Do not change the commercial rollout just to reconcile the copy.

## FAQ and About

Keep About as a trust page: who maintains Job Seek, why it exists, where postings come from, and how to contact the team. Remove the same immediate-notification and comparative-speed claims. Add a useful final action to find companies or understand watchlists; the current closing links mostly address code, policies and contact.

Prioritize these FAQ answers above operator/crawler questions:

- Do I need to know which companies I want to follow? Explain optional company filtering.
- When are email digests sent, and how do I enable or pause them?
- Can I track a job I found on another website? State the current indexed-job scope.
- What happens if a company is missing? Describe the request process.
- What does free include, and what does Pro add?
- Does remote mean I can work from any country? Explain that posting restrictions still apply.
- Does a posting's language establish its working language? No; direct users to the employer's requirements.

Group employer/operator questions separately or link to the indexing policy so job seekers can reach decision-relevant answers quickly. Keep every visible answer aligned with its JSON-LD representation.

## Search acquisition: add useful pages, not persona permutations

Recommended first additions, in order:

1. **A company-job-alerts page:** selected employers, source coverage, a demonstrated saved-search flow, opt-in weekly timing, sample digest, free limits, and a CTA into the matching workflow. Own the specific alerts query family here; keep the homepage as the broader product introduction.
2. **A practical guide to monitoring company career pages:** explain native company alerts, LinkedIn company alerts, and a multi-company monitoring workflow. Use dated, verified examples and honest differences.
3. **A scoped application-tracker page:** show save → applied → interview notes, explicitly tied to jobs on Job Seek. Do not target import/template promises that the page cannot deliver.

Keep Explore noindex. Add localized canonical/hreflang/sitemap entries only when a new public page contains meaningful translated content, and connect it from the homepage/FAQ and relevant guides. Validate local phrasing rather than just copying the English search term.

The [blog](https://jseek.co/en/blog) currently has three editorial subjects: a welcome post, an indexing explanation, and a dated remote-engineering analysis. Retain useful original research, but add practical job-seeker workflows; the publication currently emphasizes infrastructure and industry analysis more than getting a specific task done.

Keep the May remote-engineering article visibly historical; its date is already in the title. Improve the route from that dated snapshot to a current filtered search. Do not change the date without recomputing the evidence. Its discussion also overreaches the snapshot in places: a single sample does not establish that remote work is “consolidating” over time, and 31 *tagged* entry-level roles does not establish that only 31 roles are available to juniors when 36% lack a seniority tag. Tighten those conclusions. Treat multi-tag remote/onsite postings as ambiguous until checked, not automatically optional office access. These are interpretive corrections, not a claim that the historical counts have been disproved.

## Implementation order and validation

**First change:** correct claims, clarify cadence, tracker scope and trial availability, improve CTA labels/free reassurance, update all four catalogs and metadata together. Keep the existing design and feature order.

**Second change:** publish the alerts page and methods guide with actual workflow proof; add the scoped tracker page if its narrower promise remains useful.

**Then:** test one employer-discovery guide and one non-English/non-software example after inventory checks. Evaluate landing-page visits through useful search, relevant watchlist creation, alert opt-in, retained use and Pro activation. The persona counts are not audience weights, and proposed conversion improvements remain hypotheses until observed.

For an implementation PR, verify localized text coverage, visible FAQ/JSON-LD consistency, mobile CTA flow, sign-in return paths, actual notification availability and timing, and page indexing metadata. This review itself changes documentation only; application builds were unnecessary.
