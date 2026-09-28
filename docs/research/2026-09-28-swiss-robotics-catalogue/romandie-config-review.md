# Robotics catalogue verification: Romandie and Bern

Verified 28 September 2026. Counts describe the reviewed source at that time, not a promise of ongoing vacancies. Configurations were prepared through `WS_LOCAL=1` in the isolated catalogue worktree; no production records were edited directly.

## Configured employers

| Employer | Configuration | Verification |
| --- | --- | --- |
| CASCINATION (`cascination`) | `personio`, tenant `cascination-ag`; scraper `skip` because the XML supplies complete descriptions | All **3 Bern** positions match the [official Personio board](https://cascination-ag.jobs.personio.com/): AI Solutions Engineer; Software Developer – Real-Time Rendering & Computer Vision (C++); System Integration and Test Engineer. All have substantive, distinct descriptions (2,520–3,795 characters), location, full-time employment and posting date. |
| RigiTech (`rigitech`) | `sitemap`, filtered to canonical `https://rigi.tech/job/<slug>/` paths; `json-ld` scraper | Exactly **1 Prilly, Switzerland** role matches the [official careers page](https://rigi.tech/careers/): [Marketing and Sales Internship](https://rigi.tech/job/marketing-and-sales-internship/). The complete description preserves marketing/sales duties, qualifications, summer/fall 2026 start, six-month duration, language requirements and application instructions. Posting date is preserved. |

RigiTech's WordPress RSS was tested and **rejected**: it returned the right title and count but only a 152-character company summary and no location. Treating that feed as complete rich data would skip the required detail extraction. Sitemap plus JSON-LD supplies the complete role-specific content. The automated link guess also mistook unrelated PDF links for jobs; the final URL filter only admits the actual `/job/` routes.

All three CASCINATION descriptions and RigiTech's sole description were read and compared with source content. Optional structured fields absent upstream remain absent rather than inferred. Both company quality gates returned zero blockers and zero warnings. Final local submits completed successfully after all shared CSV stubs were populated; global CSV validation and `git diff --check` passed. `WS_LOCAL=1` skipped Git/GitHub mutations, leaving changes uncommitted for the combined PR. Official logo artwork was visually inspected; original logo/icon files and EN, DE, FR and IT descriptions were prepared for the normal image-upload and catalogue-sync pipeline.

Product and location sources: [CASCINATION company](https://www.cascination.com/en/about-us), [RigiTech careers](https://rigi.tech/careers/). CASCINATION is a Bern surgical robotics/image-guidance employer; RigiTech is a Prilly delivery-drone and fleet-software employer.

## Investigated employers without a usable configuration

### LEM Surgical

The [current official careers page](https://lemsurgical.com/lem-surgical-job-opportunities/) returns HTTP 200 and lists **Systems & Field Engineer** and **Test & Commissioning Engineer**, both in Bern, followed by an email contact. Its page metadata says it was modified on 11 August 2026.

The current HTML contains no individual job links, linked job PDFs, embedded job-board iframe, JobPosting structured data, or substantive descriptions of either role. Search-visible engineering PDFs uploaded in 2024/2025 are not linked from this live page and were not treated as current vacancies. The [official LinkedIn identity](https://www.linkedin.com/company/lem-surgical-ag/) has organization ID `79753723`; exact-ID public guest discovery returned zero jobs and therefore does not cover the two visible roles.

**Decision:** no company or board stub added. A working source must provide real job identities and complete descriptions; fabricating URLs or importing unlinked historical PDFs would not meet that requirement. This is a source-coverage gap, not a claim that LEM has no vacancies.

### BlueBotics

The [official BlueBotics website](https://bluebotics.com/) links its [LinkedIn company](https://www.linkedin.com/company/bluebotics-sa/) and parent ZAPI Group. The [ZAPI careers page](https://www.zapigroup.com/en/careers) directs its “See open positions” link exclusively to [ZAPI Group LinkedIn jobs](https://www.linkedin.com/company/zapi-group/jobs/). That page exposes an affiliated-company filter containing `111080`, independently confirmed as BlueBotics' organization ID by its own public company page. This establishes source ownership without assigning unrelated parent-company jobs to BlueBotics.

Exact-ID guest-job discovery returned zero BlueBotics jobs; exact-slug discovery also found no verifiable posting. No explicit official empty-board statement or current complete individual posting could be confirmed.

**Decision:** no company or board stub added. An empty guest response alone was not accepted as proof of a verified empty board. Revisit when the official feed exposes a complete posting or an explicit empty state. BlueBotics remains an active robotics employer in [Saint-Sulpice](https://www.zapigroup.com/en/contact-us); the gap concerns reliable job monitoring.
