# Narrowed prompt validation

Date: 28 September 2026. These are live product checks and manual description
reviews, not a statistical accuracy benchmark. Counts are a dated snapshot.
No model decisions were overridden for the screenshots.
## Current marketing request

> I want to work directly with users and turn their problems into product improvements. No people management.

The short request replaces the longer experiments below. It was run against the
20-company Switzerland / Software Engineer watchlist and checked manually.

| Request language | Accepted | Rejected | Evaluated |
| --- | ---: | ---: | ---: |
| English | 4 | 63 | 67 |
| German | 6 | 61 | 67 |
| French | 4 | 62 | 66 |
| Italian | 4 | 62 | 66 |

All runs reached `caught_up`. One posting left the active corpus between runs.
The showcase was restored to the English request and reached `caught_up` with
4 accepted / 62 rejected / 66 evaluated at 11:26 UTC on 28 September.

English, French, and Italian retained Microsoft Senior Software Engineer,
NVIDIA Senior CPU Performance Developer Technology Engineer, NVIDIA Developer
Technology Engineer (Energy), and Swisscom IT Consultant & Full Stack Software
Engineer. German also retained the two Google roles discussed below. The
Microsoft/NVIDIA/Google descriptions were reviewed in the earlier iterations;
the Swisscom role was checked against its original German description, which
explicitly combines understanding customer needs and building applications.
The short request does not exclude client projects. The stored English version
of that Swisscom description was anomalous and was not used as manual evidence.

The marketing preview reuses the actual read-only `AiSearchFilter` drawer and
`CompanyIcon`, populated with public fields from these recorded results in
`apps/web/src/content/narrowed-results.json`. It does not make live requests,
show fictional matched/excluded quotations, or present these checks as a
benchmark. Translated previews use the corresponding tested result sets.

## Historical prompt experiments

The following longer prompts and counts are retained as research history. They
are superseded in the page, shared pricing preview, and screenshot watchlist.


## Engineering: turning user problems into product improvements

Watchlist: `940c2f38-6501-44ad-8d47-8c4bca7d4443` in the screenshot account.
20 saved employers; Switzerland and Software Engineer filters; 67 active roles.
Employers: ABB, Adobe, Amazon, Apple, Cisco, Datadog, Elastic, GitLab, Google,
IBM, Logitech, Meta, Microsoft, NVIDIA, Oracle, Proton, Salesforce, SAP,
Siemens, and Swisscom.

### Iteration and evidence

1. Product ownership without people management: **23 accepted / 44 rejected**.
   The distinction was meaningful, but it was a broad example of senior
   individual-contributor work.
2. Turning customer problems into product improvements: **26 accepted / 41
   rejected**. Manual review found the wording too permissive. Datadog's Staff
   Software Engineer — Security Agent describes substantial product ownership
   and calls the candidate customer-focused, but does not explicitly describe
   working with users or customer-facing teams to investigate their problems.
3. Require both parts of the feedback loop, with explicit evidence:
   **7 accepted / 60 rejected**, all 67 evaluated; state `caught_up`.

Exact tested request:

> Find hands-on engineering roles that close the loop from a real user problem to a reusable product improvement. The posting must explicitly mention BOTH working with users, customers, community reports or customer-facing teams to understand problems AND building or changing the product in response. Generic 'customer-focused' language is not enough. Exclude ticket handling or bespoke client delivery without changes to the shared product; exclude people-management roles.

This expresses a relationship between responsibilities. A search for
“customer”, “product”, or “senior” cannot establish that relationship, and
excluding those words would also remove valid jobs.

### Manual checks of every retained role

The reviewer read the stored description used by Narrowed for all seven roles.
These summaries are the reviewer's interpretation, not model explanations.

| Role | Why the retained result makes sense | Source description |
| --- | --- | --- |
| Microsoft — Senior Software Engineer | Owns VS Code features through delivery, investigates community issues, works directly with developers, and uses feedback after release. | [Stored description](https://jobseek-assets.colophon-group.org/job/36e9bcb3-318f-4116-b23e-c61fe6f593df/en/latest.html) |
| Microsoft — Principal Software Engineer | Hands-on prototypes and implementation, community-reported issues, direct work with extension authors, and feedback-led product decisions. Technical influence explicitly does not depend on reporting-line authority. | [Stored description](https://jobseek-assets.colophon-group.org/job/ed5336ee-68f0-4cc4-8469-6d0937f90496/en/latest.html) |
| Google — Software Engineer III, Cloud AI for Science, Full Stack | Works with customers to close product and quality gaps, develops scientific agents, and iterates on tools against customer needs. | [Stored description](https://jobseek-assets.colophon-group.org/job/c917a56b-cfed-4537-875d-597c39524e31/en/latest.html) |
| Google — Staff Software Engineer, Home and Health Infrastructure | Direct collaboration with end users to find friction, root-cause investigation, and product evolution through experiments and metrics. “Infrastructure” in the title does not mean purely operating systems. | [Stored description](https://jobseek-assets.colophon-group.org/job/4daf49f8-81a2-4113-8c40-b59d0de9f794/en/latest.html) |
| NVIDIA — Senior CPU Performance Developer Technology Engineer | Works directly with the developer community, contributes to reusable stacks/libraries/reference code, and uses that work to improve future architectures and software. | [Stored description](https://jobseek-assets.colophon-group.org/job/7d9d9839-e8cc-4ee6-a741-3d10dc3d10fa/en/latest.html) |
| Elastic — Senior Software Engineer, Vector Search | Handles community issues and pull requests while developing shared Elasticsearch features and fixes. | [Stored description](https://jobseek-assets.colophon-group.org/job/9d617605-97c8-452f-b5c2-26bf1cfc775e/en/latest.html) |
| NVIDIA — Developer Technology Engineer, Energy | Borderline but defensible: includes customer-specific patches and engagements, but explicitly also creates reusable libraries for future products and influences the roadmap from customer requirements. The request excludes bespoke delivery *without* shared-product changes. | [Stored description](https://jobseek-assets.colophon-group.org/job/4cfa732f-8d2f-4fd1-8d75-3c8f0ab6d7ff/en/latest.html) |

### Checks of excluded roles

- **Datadog — Staff Software Engineer, Security Agent:** excluded by the final
  prompt. Product ownership and generic customer focus alone do not establish
  the requested feedback loop. [Description](https://jobseek-assets.colophon-group.org/job/7153d983-9435-4a2b-a40b-c3b0ddd3c300/en/latest.html).
- **Proton — Senior Backend Engineer (Account):** excluded by the final prompt.
  Strong product ownership and customer-centricity are described, but direct
  user-problem investigation and feedback-led changes are not explicit. It was
  correctly plausible for the earlier ownership prompt. [Original posting](https://job-boards.eu.greenhouse.io/proton/jobs/4739749101).
- **Elastic — Principal Software Engineer, Search Infra:** excluded by the final
  prompt. It describes product/partner collaboration and technical direction,
  but not the explicit user-problem-to-product feedback loop. It was plausible
  for the earlier ownership prompt. [Description](https://jobseek-assets.colophon-group.org/job/78ae2008-826a-462e-897a-bb6a22a9c2d3/en/latest.html).

The checks support this example's usefulness. They do not establish perfect
precision or recall, and missing evidence is not proof that the real job lacks
that responsibility.

## Shared pricing illustration: exact short request

The shared `ProPitch` uses a shorter request, so that exact wording was also
run through the live Narrowed API rather than assuming the longer request's
results apply to it:

> Hands-on ownership from design to production, without people management—even if the title says senior or staff.

Test corpus: 22 software-engineering roles in Switzerland, following ABB,
Google, and Siemens. Result: **16 accepted / 6 rejected**, `caught_up`.
The temporary filter was removed afterward to restore the plain alerts showcase.

The retained descriptions were read. Particularly clear matches include Google's
Commerce Actor Safety role (owns components across their full lifecycle),
Search Ads Quality and AI role (explicit end-to-end ownership), and Staff SRE
Connections role (inception/design through deployment and refinement). The SRE
result is intentional: this shorter request does **not** exclude infrastructure.
Some roles describe lifecycle responsibilities in general engineering boilerplate;
this wording is useful for broad ownership screening but less discriminating
than the explicit user-feedback-loop request above. No claim of perfect recall
or precision is made. The two small illustration cards remain explicitly
fictional, not excerpts or explanations for actual job decisions.

## Drafts excluded from the page

The quality/process-improvement and investigative customer-support ideas have
**not** completed live tests. They were removed from the public page source;
plausible wording alone is not validation. Candidates for a later test:

- Recurring-failure prevention: require root-cause investigation **and**
  implementing or verifying cross-team corrective action, rather than routine
  inspection, lab testing, or audit paperwork alone.
- Customer education: require investigating unfamiliar technical problems
  **and** teaching customers to solve or avoid them; exclude sales targets,
  upselling, and scripted first-line support.

The live browser hit HTTP 429 during additional watchlist setup and localized
screenshot capture. The latest response requested a retry after approximately
12:00 UTC. No rate-limit bypass was attempted. The final 7-result screenshot
is pending; the earlier 23-result images must not illustrate the refined request.
The current page shows the verified text and dated result count until a matching
capture is available. The new page remains subject to the user's UI review
before publication.

## Exact translated requests: independent live checks

Every retained proposal was also run in German, French, and Italian. These
single runs are evidence of wording sensitivity, not controlled model evals.
All runs evaluated the complete corresponding corpus and reached `caught_up`.

| Request | English | German | French | Italian |
| --- | ---: | ---: | ---: | ---: |
| User-problem-to-product feedback loop, 67 roles | 7 | 11 | 6 | 10 |
| Short ownership request, 22 roles | 16 | 9 (revised) | 17 | 19 |

The first German ownership wording returned just 1/22. The revised wording explicitly names software roles and responsibility across
the lifecycle, avoiding the ambiguous “Eigenverantwortlich” and operational
emphasis of “Produktivbetrieb.” This is a wording improvement, not a proven
causal explanation for the count difference. It was replaced and retested as:

> Ich suche praktische Software-Engineering-Rollen mit Verantwortung von der Konzeption bis zur Produktion, ohne Personalführung. Senior- und Staff-Titel sind in Ordnung.

The revised wording returned 9/22. The English, French, and Italian short
requests remain the exact strings stored in `pro.example.request` in their
catalogs. Main-page requests are stored in `marketing.narrowed.example1`.
Each localized main page uses the count from its own request's live run.

### What the extra review found

- The German and Italian feedback-loop runs retained three additional Elastic
  roles: Senior/Principal Search Algorithms and Principal Vector Search. Their
  descriptions explicitly combine community issues/PRs with shared-product
  fixes. These look like **false negatives in the English run**. The French run
  also omitted the Senior Vector Search role retained in English.
- German additionally retained Cisco's Principal Linux Kernel role. It involves
  customer/product collaboration and reusable open-source development, but the
  specific investigation of a user problem is less explicit. Treat it as a
  borderline result, not evidence of perfect precision.
- Additional ownership results were read: Google's Home/Health roles, XR World
  Context, Shopping Ads, and Automated Correctness and Testing describe hands-on
  design/development and delivery. Some use broad lifecycle boilerplate rather
  than explicit individual product ownership, so the short request remains a
  broad screen. It must not be advertised as a strict ownership guarantee.
- Wording and language materially change the shortlist. The page retains the
  instruction to verify original postings; do not promote these examples as
  exhaustive or language-invariant matching.

The main engineering showcase was restored to the tested English request and
confirmed at 7/67. The temporary filter on the 22-role alerts showcase was
removed after the last test. No accepted/rejected decision was manually
changed to improve a screenshot or marketing claim.
