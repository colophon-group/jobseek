# B01 — Swiss robotics employer guide

- Brief: B01 in `../blog-keyword-gap-report.md`.
- Article: `apps/web/src/content/blog/robotics-companies-zurich-lausanne.mdx`.
- Audience: English-speaking candidates considering Zurich and the Lausanne area, including production, design, quality and supply-chain backgrounds.
- Target questions: robotics companies Zurich; robotics companies Lausanne; robotics companies Zurich Lausanne careers.
- Language: English source, as specified in B01. The recurring series includes separately localized and market-specific briefs; there is no claim that untranslated pages exist.
- Evidence checked: 28 September 2026.
- Intended public route: `/en/blog/robotics-companies-zurich-lausanne`.

## Employer evidence

All six employers have configured career boards in `apps/crawler/data/boards.csv`, public catalogue pages that returned HTTP 200 and populated job lists, and matching company registry slugs. The dated public-page captures are in `b01-catalogue-check.json`. We intentionally do not publish their volatile counts, employee counts, funding, or comparative rankings.

| Employer | Original sources checked | Claim used | Catalogue destination |
| --- | --- | --- | --- |
| ANYbotics | https://www.anybotics.com/about-us/careers/ ; https://www.anybotics.com/robotics/ ; https://jobs.lever.co/anybotics | Autonomous legged inspection robots; Zurich base; locally observed test/production/internship roles | `/en/company/anybotics` |
| Voliro | https://voliro.com/careers/ | Physically interacting aerial inspection robots; Zurich address; catalogue Senior Cloud Engineer and spontaneous application | `/en/company/voliro` |
| Wingtra | https://wingtra.com/company/ ; https://wingtra.com/company/career/ | Surveying/mapping drones, sensors and software; engineering/manufacturing in Zurich; catalogue assembly, junior software and procurement examples | `/en/company/wingtra` |
| Flyability | https://www.flyability.com/company-page/ ; https://www.flyability.com/career | Indoor inspection drones/software; Paudex headquarters; catalogue sensor-validation, UX/UI and spatial-AI roles tagged Vaud | `/en/company/flyability` |
| Distalmotion | https://www.distalmotion.com/ ; https://www.distalmotion.com/contact-us ; https://apply.workable.com/distalmotion/ | DEXTER surgical system; Epalinges address; catalogue manufacturing and complaint/PMS roles tagged Vaud | `/en/company/distalmotion` |
| Ecorobotix | https://ecorobotix.com/en-us/ ; https://ecorobotix.com/en-us/crop-care/plant-by-plant-ai/ ; https://ecorobotix.com/fr/nous-contacter/ | Precision spraying with AI/optics; Yverdon-les-Bains; catalogue contains overseas roles and an unspecified-location result | `/en/company/ecorobotix` |

Product descriptions are paraphrased, not copied. Role titles are short identifiers from the dated catalogue check. Advice about which work might suit the reader is editorial interpretation, not a claim that an employer offers every suggested discipline or is recruiting now. A spontaneous application is explicitly not counted as a specific vacancy. Headquarters location is not substituted for job location.

## Internal-link policy

The article uses existing `<Company>` elements for employer navigation, existing `<JumpLink>` elements for section navigation, and `<Watchlist>` inline mentions for its combined employer feed. The watchlist element reuses `MentionPill` and the existing Eye icon; its reviewed name and shared path come from an offline repository snapshot. It has no external article-body URLs. Original evidence stays in this research record; the reader follows catalogue pages for current listings and original applications. Existing blog outbound links were reviewed: LinkedIn help, the reports/return-to-office announcements, legislation, GeoNames and source code substantiate claims or provide resources not replaced by a company catalogue page. They are not equivalent employer-browsing destinations and remain intact.

The authoring guide now records this policy for future articles and corrects the stale English-only sitemap and cadence-demotion statements. The cadence is one evidence-ready article per week, not an SEO ranking promise.

## Product behavior checks

Public company pages expose `Save this search`. Shared watchlists offer `Clone`: guests can customize a temporary browser copy, while signing in enables account persistence, sharing and alerts. Email digests require verified email and selected watchlists, and run weekly. Applications happen at the original employer; the tracker records user-managed progress for jobs saved in Job Seek. These descriptions match the released FAQ and marketing pages and their underlying implementation. The initial six-company draft did not propose a Narrowed request; the expanded shared watchlist now uses the criteria documented below.

## Review and release record

- Cold reader: SHIP, no material changes requested.
- Independent fact-check: found the unsupported interview-notes claim; removed it from the article and corrected the inherited homepage/tracker/FAQ/README wording. Tracker schema, actions, types and UI support status, interview round/type/date, not free-text notes. Final verdict: SHIP after re-reading the corrected article.
- Editorial: PUBLISH AS-IS after removing repetitive explanations/caveats and clarifying the Lausanne-area introduction.
- Local production build and 63 focused blog/mention tests passed. Six browser combinations (320/390/1440 pixels, light/dark) passed: all six company mentions and both shared-watchlist mentions resolved, no external article-body links or horizontal overflow, section jumps work, Article JSON-LD and EN/x-default metadata are present, untranslated paths redirect to English, blog index includes the article, and the OG image renders. Catalogue company destinations returned 200 with populated lists.
- Release: normal PR and production workflow; deployment outcome is recorded in the release PR.

PMS terminology was cross-checked against [Swissmedic’s post-market surveillance presentation](https://www.swissmedic.ch/dam/swissmedic/en/dokumente/stab/veranstaltung/mep-2021/ueberwachung-nach-inverkehrbringen.pdf.download.pdf/7_ueberwachung_inverkehrbringen_vigilance_und_marktueberwachung-en.pdf). The original Workable role body was inaccessible to the critic; the article makes no claims about that role beyond its observed title, location and this standard acronym expansion.

## Initial shared employer watchlist

- Canonical shared path: `/watchlists/c47beab8-3e96-4032-af4b-d9843bdba631`; title: Swiss robotics employers.
- Initial six persisted company selections: ANYbotics, Voliro, Wingtra, Flyability, Distalmotion, Ecorobotix. Location: Switzerland. No occupation restriction; all posting languages.
- Verified after reload in the owner editor and an anonymous browser: all six pills, Switzerland, populated results including Flyability. The feed had 29 active jobs at verification; this volatile count is deliberately absent from the article and snapshot.
- Guest `Clone` produced an editable browser copy. The interface explains that account login enables persistence, sharing and alerts.
- The two article mentions resolve to the same reviewed shared UUID. Desktop/mobile and light/dark checks passed.

### Company reference repair

Flyability was configured in the crawler registry and searchable in Typesense, but missing from the web database’s `company` table. Saving its selection failed the `watchlist_company` foreign key. Current crawler registry sync intentionally does not mirror companies to the web database, so re-running it would not repair this boundary gap.

Used `apps/web/scripts/repair-company-reference.ts` to insert only the missing Flyability row with canonical ID `b2a619ff-f2fd-4a37-b87a-dc211ac62d74`. The script defaults to dry run, verifies Typesense identity and registry data, refuses ID/slug conflicts, and applies one guarded transaction. All inserted fields, artwork and JSONB extras were read back and compared with the registry. Existing company rows and user data were untouched. Flyability then saved successfully through the normal watchlist UI. This scoped repair does not resolve the general legacy-reference dependency for future catalogue additions.

## Expanded employer guide

After the user approved publication and reuse of the blog layout, they requested a broader company list. The expanded article references 29 unique employers, with the intended watchlist covering the same set, Switzerland only and no occupation restriction. The 23 additional entries are concise regional descriptions; the original six examples retain their deeper job-search context.

The primary-source audit, parent-brand decisions and unresolved source gaps are in `b01-expanded-employer-evidence.json`. Catalogue PR #10160 adds five employers and repairs roboa/Verity coverage before the article release. LEM Surgical and BlueBotics were investigated but not configured from incomplete or stale hiring material. SwissDrones' Buchs SG site is distinguished from Swisslog's Buchs AG site. The expanded shared watchlist uses Narrowed to remove unrelated product lines at diversified employers rather than leaving readers to filter those roles manually.

All three independent reviewers cleared the expanded draft: factual SHIP, editorial SHIP, product/factual SHIP conditional on verifying all 29 saved memberships and the five new catalogue destinations before publication.

## Narrowed verification

Saved criteria: “Keep work on physical robots or autonomous machines, including their sensors and control software. Include production, quality, procurement, sales and customer support tied to those products. Exclude unrelated product lines, even at robotics companies.”

The request is short enough to read in the actual interface and intentionally crosses occupational categories. It requires a product connection rather than a robotics keyword or employer label.

On 28 September, a reproduction using the real first five complete job descriptions accepted ANYbotics Product Manager – Physical AI and Mimic Senior Mechanical Engineer (Wearable Devices). It rejected Stäubli Project Leader Renewable Energy, ABB Sales Specialist Panel Builder and ABB R&D Center Lead – Materials Development. Manual description review agreed with these five decisions, including Stäubli's generic robotics boilerplate, which did not make the renewable-energy role relevant.

The initial production attempts returned provider errors with no persisted decisions. A local reproduction is not evidence that the shared feed works. PR #10161 added safe HTTP-status diagnostics without changing classification or billing behavior. Successful live processing and guest verification are required before publication; the final production outcome and deployment checks are recorded in the release PR.

Guest cloning was tested after enabling Narrowed: the interface warns that a Free copy keeps the standard filters and cannot use its own Narrowed feed. The article explains that Pro is required for Narrowed in the reader's own copy, while the existing shared feed remains viewable.

Expanded-draft verification: production build and all 63 focused tests passed. Six light/dark × 320/390/1440 browser checks passed with all 29 employer mentions resolved, both watchlist mentions intact, internal navigation and SEO metadata intact, and no horizontal overflow. The Narrowed passage passed a separate editorial/product review, conditional on live verification.

A second bounded local test used five real lifecycle roles and complete descriptions: Wingtra Production Operator Assembly, ANYbotics Senior Product Quality Engineer, RIVR Procurement Engineer, Distalmotion Clinical Sales Specialist (including clinical training/customer support), and BOTA Robotics Engineer for Sales. The unchanged criteria accepted all five, consistent with manual review of their direct robotics-product responsibilities. This checks that the prompt does not collapse into an engineering-title filter; it remains local evidence until production decisions are persisted.
