# Job Seek content-rights assessment

27 September 2026. Prepared by Codex at the owner's express request to conduct
the review. Tracks [#10079](https://github.com/colophon-group/jobseek/issues/10079).
This is a source-supported risk assessment, not an opinion from a licensed lawyer
or a certification of compliance. An external review is not an acceptance
requirement imposed by this task.

## Decision

**The paid filtering concept is defensible in principle; the current complete
content pipeline does not support an unqualified assurance of content rights.**
Charging US$10/month for Narrowed does not itself make use unlawful. Conversely,
keeping descriptions free does not establish permission to reproduce them.

My recommendation is to retain the seeker-paid filtering model, establish a
recorded basis for each source and use, and address the concrete defects below
before representing content readiness as complete to Paddle. The weakest uses
are unrestricted full-description republication and the public full-text
training dataset. Internal factual extraction and matching have materially
stronger arguments, subject to lawful access, reservations, applicable contracts,
territory and retention. Do not require a written licence for unprotected facts
or uses that actually satisfy an exception.

The category inquiry already sent to Paddle can proceed. Its response will not
decide employer copyright or database rights. This assessment recommends no
blanket statement that all scraping infringes and no blanket fair-use assurance.
It has changed no crawler configuration or job records. During review, the owner
explicitly instructed that all of their HF datasets be made private; the Job Seek
dataset visibility changes below were then applied and verified.

## Scope and evidence

Implementation reviewed: `981404556429fa2ff7a2a1bdcc97e806b6704dc9` on the isolated
Paddle preparation branch, based on main
`4966bcde0ec0be2018f7117242818181401c0979`. The
[original brief](content-rights-review-brief.md) records the business facts and
representative source inventory. The owner reports no negotiated source licences.

Collection is from employer career sites and ATS interfaces. Job Seek stores
descriptions, displays their sanitized HTML, links to the source, and sends
description text to TypeSafe for Narrowed evaluation. The 12,000-code-point
classifier limit can encompass a whole job advertisement; it is not a legal
excerpt limit. Production infrastructure includes German hosting and US service
providers, as recorded in [the infrastructure evidence](production-privacy-evidence.md).
Swiss establishment and a Swiss governing-law clause in Job Seek's customer
Terms do not establish that only Swiss IP law applies to these separate acts.
This review assesses Swiss law, the relevant EU/German framework and US fair use;
it does not purport to decide every source country's law or a court's jurisdiction.

Public source pages, ATS documentation and published supplier terms were checked
on the review date. Some ATS pages expose client-rendered content without legal
links in the fetched HTML. No employer customer agreement, source licence or
contract assent was inferred from that absence. No authentication barriers were
bypassed. Source sampling is representative, not clearance of the whole registry.

## Assessment by use

| Use | Assessment | Conditions / resulting action |
| --- | --- | --- |
| Job facts, employer names and original links | Generally defensible as to copyright in the individual facts | Preserve provenance and accuracy. Separately evaluate database extraction, contract restrictions and misleading affiliation. An entire source database is not equivalent to one unprotected fact. |
| Necessary internal copying for indexing, structured extraction and Narrowed | Plausible, conditional basis | Record permission or the applicable exception; check lawful access, source reservations, outsourced processing and retention. Current TDM controls are incomplete. |
| Entire descriptions displayed on Job Seek | Unresolved at source level; insufficient basis for a fleet-wide assurance | Use a documented syndication permission or a specific, reasoned basis for the material. For unsupported protected wording, recommend original factual summaries and source links; short quotations still need an applicable purpose and attribution. |
| Employer logos | Conditional; distinct from factual names | Record asset origin and permission or a defensible referential use. Avoid suggesting sponsorship. Copyright in the image remains a separate question from trademark identification. Use a text name where the image basis is unresolved. |
| Public full-description labelled dataset | Public exposure contained during review; reuse basis remains unresolved | The owner authorised private visibility, now verified. Keep the dataset private pending a row/source rights decision; private research copies still need their own basis. Prior downloads cannot be recalled by changing visibility. |
| Job Seek's own code, schemas and original contribution | May be licensed to the extent rights exist | Do not claim ownership of employer wording, logos, bare facts or necessarily all machine-generated labels. The proposed policy exclusions help but do not cure the dataset's independent notice or source rights. |

These assessments are recommendations about the evidenced implementation, not
findings that every copied description is protected or that a court has found an
infringement.

## Legal reasoning

### Switzerland

The [Copyright Act, Arts. 2, 4, 10, 24d and 25](https://www.fedlex.admin.ch/eli/cc/1993/1798_1798_1798/en?version=20250701)
distinguishes individual creative expression from facts, protects sufficiently
original collections separately, reserves reproduction/making available, and
provides specific research and quotation exceptions. Scientific-research copying
requires lawful access and a technically necessary process. Quotation requires
a justified explanatory, referential or illustrative purpose and source credit.
The English translation is informational; the official-language texts govern.

Application: title/location/salary facts and routine functional phrases have a
stronger position than an employer's original narrative. A field-by-field
assessment is preferable to declaring every job description protected. Ordinary
subscriber matching is not established here as scientific research. A real
extractor research project might qualify, including in a commercial setting;
that does not establish a right to publish its full-text corpus to everyone.
Displaying an entire advertisement for the same reading purpose is not made a
quotation merely by adding a source link. Durable description storage should not
be characterised as incidental transient caching.

The [IPI's public-domain guidance](https://www.ige.ch/fileadmin/user_upload/schuetzen/urheberrecht/e/Public_Domain_Fact_Sheet_EN_04.2020.pdf)
explains both the freedom of factual content and the Swiss/EU difference in
database protection. There is also a separate Swiss unfair-competition question:
in [BGE 134 III 166, Documed v ywesee](https://www.bger.ch/ext/eurospider/live/fr/php/clir/http/index.php?highlight_docid=atf%3A%2F%2F134-III-166%3Ait&lang=it&type=show_document),
the court rejected copyright protection for the particular functional drug texts
and assessed appropriation under Art. 5(c) UWG, including the producer's recovered
investment. This supports examining actual originality and investment, not
equating copying with liability. It does not decide the result for Job Seek.
Our own normalization and search investment is relevant context, not an automatic
defence to every source's rights.

### EU/German copying and database rights

[DSM Directive Art. 4 and recital 18](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX%3A32019L0790)
provide a route for commercial text/data mining of lawfully accessible material
where rights have not been appropriately reserved. This concerns reproduction
and extraction, not a general right of full-text public redistribution. The
German implementation, [UrhG §44b](https://www.gesetze-im-internet.de/urhg/__44b.html),
requires deleting copies when no longer necessary and recognises machine-readable
reservations for online works. Checking just one HTTP header is not proof that
all legally effective reservations have been checked. Website terms and other
signals require contextual assessment; silence is not a universal licence.

Application: analysis of posting text to return factual matches is a credible
TDM candidate. The actual source access, reservations, German copies and later US
processing must each fit the basis. A German exception cannot simply be exported
to cover every foreign act. Job Seek is not shown to qualify for the special
research-organisation route under Article 3. A full description shown to users,
or supplied as a downloadable corpus, needs a separate basis.

[CV-Online Latvia v Melons, C-762/19, especially paras. 41–47](https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX%3A62019CJ0762)
directly concerns a job-advertisement search engine. The Court treated substantial
copying/indexing and searchable reuse as extraction/reutilisation, while requiring
assessment of qualifying investment and the risk to its recovery. It also
recognised the value of innovative information aggregation. Accordingly, factual
results and links alone do not eliminate database risk, but neither does this
case ban job-search engines.

[British Horseracing Board v William Hill, C-203/02](https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX%3A62002CJ0203)
distinguishes investment in obtaining existing materials from creating them.
Application: an employer creating its own vacancy is not automatically in the
same position as a commercial aggregator gathering others' vacancies. Independent
verification/presentation investment can still matter. Prefer original employer
sources, record source type and assess substantial/repeated extraction; do not
claim that this preference alone resolves the question.

### US fair use

[17 USC §107](https://www.copyright.gov/title17/92chap1.html#107) requires balancing
purpose, nature, amount and market effect. Applied to Job Seek:

- **Purpose:** internal matching and classification add a different analytical
  function. Charging for that function counts as commercial use but does not
  automatically defeat the defence. Public full-text display has a less distinct
  purpose from the original posting.
- **Nature:** published, factual recruitment content is favourable; original
  marketing narrative and expressive images are less favourable.
- **Amount:** full internal text may be needed for reliable matching. That does
  not explain why everyone needs a downloadable copy or complete mirrored text.
- **Market:** source application links can benefit recruitment. Full mirrors and
  datasets can substitute for visits or licensed redistribution; a free source
  does not prove that no relevant market exists. Actual harm is not established
  by this review.

The [Copyright Office's summary of Authors Guild v Google](https://www.copyright.gov/fair-use/summaries/authorsguild-google-2dcir2015.pdf)
supports full-text indexing with constrained public snippets that do not provide
a substantial reading substitute. It is helpful to the internal-search argument,
but does not establish a defence for unrestricted full-description publication.
The conclusion is a credible, fact-dependent US defence for analysis, materially
weaker evidence for the public corpus, and no worldwide fair-use clearance.

### Contracts and the model provider

Contract restrictions depend on the agreement's scope and applicable assent
rules. An ATS's employer customer agreement is neither automatically binding on
an unaffiliated reader nor a sublicence to that reader. Public accessibility,
robots rules, technical API documentation and express syndication permission
are different evidence and must remain distinguishable in the source register.

[TypeSafe's MCA §§4.1, 5, 12.3 and 13](https://typesafe.ai/legal/mca)
requires the customer to have the rights necessary for the contracted processing
and places input-related obligations and indemnity on the customer. It prohibits
training on customer data without consent but permits specified processing,
including some continuing fraud/compliance uses. Application: do not treat
no-training language as an input licence or zero retention. For an exception-based
source, record whether outsourced analysis and the actual contractual processing
fit that exception; otherwise use permitted factual inputs or obtain sufficient
permission. This is a content-rights question, not a reopening of #8328's broader
privacy review.

## Representative source decisions

All entries below were reviewed on 27 September 2026. A source family's shared
technology does not prove identical employer rights. “Unresolved” means the
evidence is insufficient for the specified use, not a finding of illegality.

| Source in the registry | Evidence and scope | Assessment for Job Seek |
| --- | --- | --- |
| Lever / [15Five](https://jobs.lever.co/15five) | [Lever's job-board integration guidance](https://hire.lever.co/developer/usecases) expressly supports customer XML feeds for public postings, including description/application fields. Its [public API documentation](https://github.com/lever/postings-api) also explains that published jobs are exposed to third-party scraping. | Strongest positive syndication evidence in this sample. Record the employer/feed arrangement or the specific inferred permission and its limits. Current JSON collection is not evidence that 15Five supplied Job Seek a feed or authorised every downstream use. Paid matching and public dataset sublicensing remain separate. A 15Five software-customer MSA must not be misapplied to an unaffiliated Lever visitor. |
| Greenhouse / [1-800 Contacts](https://job-boards.greenhouse.io/1800contacts) | [Job Board API documentation](https://docs.greenhouse.io/job-board.html) provides public job access and descriptions, principally for career-site integration. The sampled fetched board did not establish incorporation of the retailer's separate customer terms. | Public read access supports indexing intent but does not establish an unrestricted republication/AI/dataset grant. Facts and conditional analysis have stronger support; full-text reuse requires a recorded basis. No blanket conclusion that the retailer's shopping contract binds this crawler. |
| Ashby / [0x](https://jobs.ashbyhq.com/0x) | [Public API](https://developers.ashbyhq.com/docs/public-job-posting-api); [customer terms §§1.9, 4.1, 4.3](https://www.ashbyhq.com/resources/terms) address customer data and customer-selected third-party integrations. | Public API is positive access evidence, not a transfer of all employer rights. The customer terms cannot be treated as Job Seek's licence. `isListed=false` is excluded by the reviewed monitor. Record the use basis and retain that exclusion. |
| Workday / [2020 Companies](https://2020companies.wd1.myworkdayjobs.com/external_careers) | [Workday terms](https://www.workday.com/en-us/legal/site-terms.html), updated 13 August 2026, restrict scraping/commercial copying on covered Sites and require a separate agreement for defined Workday APIs. Scope includes referenced pages and defined APIs. The tenant HTML fetched did not establish incorporation of those terms. | Priority for source-specific contract/access review. Do not state all tenant crawling violates these terms, or that all CXS endpoints are affirmatively licensed. Capture tenant/API scope and employer policy before approving full-text/AI uses on that basis. |
| Workable / [1GLOBAL](https://www.1global.com/vacancies) | The vacancies page links [1GLOBAL's website terms](https://www.1global.com/website-terms-and-condition), dated 1 April 2026. They choose English law and contain neither an express scraping prohibition nor a syndication grant in the reviewed text. [Workable's own guidance](https://help.workable.com/hc/en-us/articles/360044587953-Why-is-my-job-appearing-on-sites-where-I-haven-t-posted-it) distinguishes unwanted scraping from chosen distribution. | No express source-site ban identified here; do not invent one. Equally, no broad content licence established. Evaluate the actual Workable endpoint and each downstream use. English contractual choice does not automatically govern all IP acts or provide an EU TDM exception. |
| Employer / sitemap: [BrightLoop / ABB](https://carrieres.brightloop.fr/fr/brightloop-converters/) | Public French careers page and configured discovery path. A specific content-reuse permission was not established in the retrieved page. | Source-specific basis remains unresolved for full text, logos and public dataset. Sitemap publication is discovery evidence. Assess actual publisher, reservation and terms before extrapolating to other DOM/RSS/sitemap sources. |

Do not represent the three public ATS APIs as negotiated partners. Equally, do
not erase Lever's affirmative job-board guidance from the analysis by describing
all API evidence as mere technical availability.

## Confirmed implementation findings

1. **Full descriptions are displayed.**
   `apps/web/src/components/search/job-detail-dialog.tsx` renders sanitized
   `descriptionHtml`; `apps/web/src/lib/use-posting-detail.ts` fetches the
   description URL when required. Source links exist in the dialog. Sanitization
   changes security properties, not copyright permission. This was a code review,
   not a fresh end-to-end production posting test.
2. **Some direct API paths bypass the TDM check.**
   `apps/crawler/src/core/monitors/{greenhouse,ashby}.py` call `client.get`
   directly. The reviewed shared client does not install a universal reservation
   hook. Offline `httpx.MockTransport` fixtures returned HTTP 200 with
   `tdm-reservation: 1` and a synthetic posting: both `discover()` calls returned
   that posting without `TDMReservedError`. No live source content was needed.
3. **The HTML reservation parser misses valid attribute variations.**
   `apps/crawler/src/shared/tdm.py` recognises a simple two-attribute meta tag;
   the same offline probe with an added `data-source="publisher"` attribute did
   not raise. This is a concrete parser gap, not proof that a live source emits
   that variation. Other transports, including Go/browser paths, need coverage
   verification before making a universal public claim.
4. **A detected reservation does not suppress stored content.**
   The `TDMReservedError` handler in `apps/crawler/src/processing/board.py`
   intentionally returns `tdm_reserved` without tombstoning. There is no removal
   of existing content in that handler. Repeated matching, retained copies and
   exports can therefore outlive the fetch decision unless separately controlled.
   A TDM reservation is not automatically a demand to erase every factual field:
   suspend the uses relying on that exception and evaluate the scope.
5. **Robots enforcement remains open.**
   Disallow enforcement remains inactive; reuse
   [#2841](https://github.com/colophon-group/jobseek/issues/2841). The owner asked
   to remove robots.txt/Disallow implementation details from public copy.
   Its completion is useful for source compliance but is not a copyright licence.
   Do not bypass access restrictions or treat a refused request as an invitation
   to rotate identity. Inventory the impact before applying broad source changes.
6. **The full-text dataset was public at the start of review.**
   The unauthenticated HF API resolves the configured old namespace to
   [`viktor-shcherb/jobseek-postings-labelled`](https://huggingface.co/datasets/viktor-shcherb/jobseek-postings-labelled),
   `private=false`, `gated=false`, revision
   `60fa46964bff22e726775f484a5cb2db1637650b`, last modified 22 September.
   Its [22 September release](https://huggingface.co/datasets/viktor-shcherb/jobseek-postings-labelled/blob/60fa46964bff22e726775f484a5cb2db1637650b/data/2026-09-22.jsonl)
   has ten rows; the first two sampled records include raw/normalised HTML and
   2,383 / 6,573 characters of description text from Workday/Ashby sources.
   No employer text is reproduced in this assessment.
   **Subsequent action:** at the owner's express instruction on the same date,
   `jobseek-postings-labelled` and `jobseek-agent-traces` were changed to private.
   Authenticated metadata now reports `private=true`; unauthenticated API checks
   return HTTP 401 for both. The initial public observation above is historical
   evidence, not their current visibility. Existing uploads preserve that setting;
   a guard and documented procedure should prevent accidental public re-release.
7. **The dataset has its own licensing and removal gaps.**
   Both the live card and `apps/crawler/src/labeller/upload.py` advertise
   `license: cc-by-4.0`, while the prose limits that grant to labels/schemas and
   attributes descriptions to employers. The unrestricted metadata is misleading
   for the mixed corpus; some extracted labels themselves reproduce wording.
   “Non-commercial research” is not a redistribution permission and needs
   reconciliation with improvement of a paid product. The company opt-out list
   filters future uploads, but the uploader neither removes all historical files
   nor deletes a remote date when its last permissible row disappears. HF Git
   history and previously downloaded copies also need an explicit response plan.

## Required work and completion evidence

The following are this assessment's recommendations, not additional requirements
claimed to have been imposed by Paddle.

| Work | Completion evidence |
| --- | --- |
| Record source/use decisions in #10079 | Per source or genuinely shared policy: origin and rights holder; applicable policy URL/version and scope; factual/full-text/logo material; collection, display, AI and dataset permissions or exception analysis; reservation signals; retention; decision owner/date. No automatic “all allowed” from a public URL. Scale the family review without declaring every employer identical. |
| Enforce the decided scope | Separate capabilities for public description, internal matching, logos and dataset export. Unknown public-text rights can fall back to independently expressed facts and original links; collection/analysis still need their own basis. Product approval is needed before materially reducing source coverage or changing default full-text display. |
| [#10090 — repair TDM controls](https://github.com/colophon-group/jobseek/issues/10090) | Header/meta fixtures through real monitor entry points; valid HTML variations; browser/Go parity as applicable; persistent source/use restriction and matching/export checks. Retention review for stored copies affected by a reservation; no accidental broad job-deletion fallback. Correct the public claim until verified. |
| [#10091 — resolve dataset redistribution](https://github.com/colophon-group/jobseek/issues/10091) | Private visibility is applied and verified for both Job Seek datasets. Inventory existing rows and historical releases, gate any future public export on a recorded redistribution basis, distinguish own contributions in the card, cover empty-date deletion and remote history. Do not claim that making a repo private recalls copies already downloaded. |
| Deferred: complete rights-removal procedure on first request | Owner expressly deferred implementation on 27 September. Not a pre-verification blocker. At that point cover claimant/scope, re-ingestion, database/index copies, R2 descriptions/logos, caches, matching copies, datasets and restoration handling. Do not claim a previously tested end-to-end guarantee. |
| Publish accurate policies and final evidence | Merge/reconcile #10087 with billing #10073, verify actual source links/attribution and removal path, and keep the rights register consistent with Terms, licence and indexing statements. Record the final permitted source/content model for Paddle. |

**Recommended owner decision:** preserve Narrowed and factual search; authorise a
source-dependent description policy with factual summaries/links for unsupported
protected text. The dataset privacy decision has been supplied and applied; the
on-site display recommendation has not. If the owner retains full mirrored descriptions everywhere,
the outstanding source-specific basis must still be supplied; accepting business
risk does not create rights.

## Accurate Paddle summary at this stage

Job Seek charges job seekers for filtering public vacancies against their
criteria. We reviewed collection, on-site display, third-party analysis, branding
and dataset publication separately. There are plausible bases for factual search
and conditional analytical copying, and positive syndication evidence for some
feed arrangements. The review found incomplete reservation enforcement and
unresolved full-text redistribution rights. The Job Seek datasets have since been
made private; further remediation and source-specific decisions remain open.
We are not representing all source content as licensed or
all uses as fair use. We will provide the final content model after those actions.

This paragraph is prepared for the owner, not sent. [Paddle's AUP](https://www.paddle.com/help/start/intro-to-paddle/what-am-i-not-allowed-to-sell-on-paddle)
and its independent category decision remain relevant. The assessment deliverable
is complete; #10079 remains open for implementation and operational evidence.

## Implementation follow-up, 27 September

- Full Terms/Privacy publication was verified after #10087 deployment; see the
  [domain packet](paddle-domain-review-packet.md) for live evidence and the tally.
- Follow-up code blocks reserved Ashby/Greenhouse API responses before capture,
  parses valid extra HTML attributes and applies available HTML metadata before
  headers. The earlier header-precedence claim was incorrect under
  [TDMRep section 6.7](https://w3c.github.io/cg-reports/tdmrep/CG-FINAL-tdmrep-20240510/).
  [Fetch-path inventory](tdm-fetch-path-inventory.md) records remaining coverage.
- Both private Job Seek HF cards and LICENSE notices were corrected in live
  commits `34aba31d62a9a97ce46e15af1262c7c90ad23fbb` (labelled postings) and
  `1fd91fc79abe079f0641f986d0878bb4baa7bfd8` (traces). Source rows were unchanged;
  both repositories still report private visibility. Historical blanket metadata
  described above is no longer the current card. Original CC-BY/MIT grants are
  scoped to owned contributions, excluding third-party content.
- Upload guards and synthetic removal tests are included in the follow-up.
  Existing dated-file scrub support is distinct from the complete cross-system
  procedure, which the owner deferred until the first request. That deferral is
  not a representation that a contact address proves tested removal coverage.
