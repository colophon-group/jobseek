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

The article uses existing `<Company>` elements for employer navigation and existing `<JumpLink>` elements for section navigation. It has no external article-body URLs. Original evidence stays in this research record; the reader follows catalogue pages for current listings and original applications. Existing blog outbound links were reviewed: LinkedIn help, the reports/return-to-office announcements, legislation, GeoNames and source code substantiate claims or provide resources not replaced by a company catalogue page. They are not equivalent employer-browsing destinations and remain intact.

The authoring guide now records this policy for future articles and corrects the stale English-only sitemap and cadence-demotion statements. The cadence is one evidence-ready article per week, not an SEO ranking promise.

## Product behavior checks

Public company pages expose `Save this search`; sign-in is required to persist a watchlist. Email digests require verified email and selected watchlists, and run weekly. Applications happen at the original employer; the tracker records user-managed progress for jobs saved in Job Seek. These descriptions match the released FAQ and marketing pages and their underlying implementation. No new Narrowed request is proposed in this article.

## Review and release record

- Cold reader: SHIP, no material changes requested.
- Independent fact-check: found the unsupported interview-notes claim; removed it from the article and corrected the inherited homepage/tracker/FAQ/README wording. Tracker schema, actions, types and UI support status, interview round/type/date, not free-text notes. Final verdict: SHIP after re-reading the corrected article.
- Editorial: PUBLISH AS-IS after removing repetitive explanations/caveats and clarifying the Lausanne-area introduction.
- Local production build and 26 focused blog tests passed. Six browser combinations (320/390/1440 pixels, light/dark) passed: all six company mentions resolved, no external article-body links or horizontal overflow, section jumps work, Article JSON-LD and EN/x-default metadata are present, untranslated paths redirect to English, blog index includes the article, and the OG image renders. Catalogue company destinations returned 200 with populated lists.
- Release: normal PR and production workflow; deployment outcome is recorded in the release PR.

PMS terminology was cross-checked against [Swissmedic’s post-market surveillance presentation](https://www.swissmedic.ch/dam/swissmedic/en/dokumente/stab/veranstaltung/mep-2021/ueberwachung-nach-inverkehrbringen.pdf.download.pdf/7_ueberwachung_inverkehrbringen_vigilance_und_marktueberwachung-en.pdf). The original Workable role body was inaccessible to the critic; the article makes no claims about that role beyond its observed title, location and this standard acronym expansion.
