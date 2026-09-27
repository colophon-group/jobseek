# Job Seek: focused content-rights review brief

Prepared 27 September 2026 as the factual scope for content-rights review. Tracks
[#10079](https://github.com/colophon-group/jobseek/issues/10079).
The owner subsequently instructed Codex to conduct the review itself. The
[completed assessment](content-rights-assessment.md) answers this brief and
records source decisions, verified defects and recommended changes. This brief
remains the scope record, not a legal clearance. No external lawyer was engaged
or made a prerequisite to completing the assessment.

## Decision requested

Can a Swiss individual operator commercially provide the described service
using the current sources and content? Please provide a scoped written assessment
by source/use/jurisdiction, identifying any required permissions or changes.
Distinguish uncertainty from prohibited uses and identify defensible conditions.
We need an actionable answer, not a general statement about fair use.

Operator: Viktor Shcherbakov, Job Seek, Route d'Oron 5, 1010 Lausanne,
Switzerland; business@colophon-group.org. The operator reports no employer/ATS
licences or permissions and no previous content-rights opinion. The intended
subscription is US$10/month for seeker-controlled Narrowed filtering; employers
are not charged for listings or advertising under this model. Public browsing,
standard filtering, watchlists and application tracking are free.

## Actual uses to assess separately

| Use | Implementation/evidence | Specific question |
| --- | --- | --- |
| Repeated collection | Public employer boards via ATS APIs, RSS, sitemaps, DOM/browser and other adapters; registry in `apps/crawler/data/boards.csv` | Which employer/platform terms apply, and what rights permit collection at this scale? |
| Storage and display | Posting metadata and descriptions are stored, indexed and presented on jseek.co with employer/source/application links | Distinguish unprotected facts, protectable wording, database/collection rights, excerpts and full descriptions. What attribution or display limits apply? |
| Matching | `apps/web/src/lib/ai-filter/classifier-input.ts` and `jev-client.ts` send user criteria plus job ID/title/company/description text to TypeSafe in the US; description text is bounded at 12,000 code points | Does the proposed basis permit copying and commercial inference by a third party, including relevant cross-border use? This is posting evaluation, not candidate ranking. |
| Branding | Company registry and staged images feed displayed employer logos; some icons use DuckDuckGo | Assess copyright/trademark/nominative-use rules and image-source terms independently of text rights. |
| Enrichment and reuse | Job Seek adds structured attributes; the repository has a CC BY-NC notice | Identify which original compilation/enrichment rights we actually hold. Third-party content is excluded from the proposed notice; changing that notice does not establish our own source rights. |
| Ancillary dataset publication | `apps/crawler/src/labeller/upload.py` targets the public `viktoroo/jobseek-postings-labelled` Hugging Face dataset, including labelled posting content | Separately assess public dataset redistribution and any affected existing releases. Permission for on-site search does not automatically cover redistribution. |

## Representative source inventory

Examples below are real registry entries at main
`4966bcde0ec0be2018f7117242818181401c0979`. They establish the collection model,
not licences. Employer-specific terms and governing laws still need to be captured
and assessed. A platform's customer agreement does not automatically bind an
unaffiliated visitor or grant that visitor a sublicence.

| Family | Example source | Primary reference and open question |
| --- | --- | --- |
| Greenhouse | [1-800 Contacts](https://job-boards.greenhouse.io/1800contacts) | [Job Board API](https://docs.greenhouse.io/job-board.html) exposes public GETs and full descriptions. Does use outside the employer's own career page have an applicable grant? |
| Lever | [15Five](https://jobs.lever.co/15five) | [Developer FAQ](https://hire.lever.co/developer/support) describes public postings access. Check employer/feed terms and redistribution permission. |
| Ashby | [0x](https://jobs.ashbyhq.com/0x) | [Public posting API](https://developers.ashbyhq.com/docs/public-job-posting-api), [customer terms](https://www.ashbyhq.com/resources/terms). Determine applicable terms and permission chain; API availability alone is not the answer. |
| Workday | [2020 Companies](https://2020companies.wd1.myworkdayjobs.com/external_careers) | [Workday site terms](https://www.workday.com/en-us/legal/site-terms.html). Check whether the actual tenant page incorporates these or employer terms; do not assume every Workday tenant has identical conditions. |
| Workable | [1GLOBAL](https://www.1global.com/vacancies) | Check the employer page, embedded service and terms actually presented there. No permission evidence supplied. |
| Employer/sitemap | [BrightLoop / ABB](https://carrieres.brightloop.fr/fr/brightloop-converters/) | Review the employer's own site terms and jurisdiction. A sitemap is discovery evidence, not a licence. |

The largest registry families include Greenhouse (2,570 boards), Ashby (935),
DOM (838), Workday (494), RSS (407), API sniffer (324) and sitemap (314). These
are configuration counts, not counts of active content or rights holders.
Review representative families first and define how the conclusion scales to
remaining sources; do not apply a single employer's permission to every board.

## Controls and limitations to include in the assessment

- Source and employer attribution, original application links, and a public
  contact/removal route exist. Inspect a live posting as part of review.
- `HowWeIndexContent.tsx` explicitly says `robots.txt` is used for discovery and
  Disallow enforcement is not active. Do not represent robots compliance as
  implemented or treat robots permission as an IP licence.
- `apps/crawler/src/shared/tdm.py`, HTTP retry helpers and several adapters check
  TDM reservation signals. Code evidence is not proof that every transport/path
  enforces every signal; any rights assessment relying on complete coverage
  should identify and test that requirement.
- Owner decision, 27 September 2026: implement the full cross-system removal
  procedure upon the first request. This is deferred operational work, not a
  prerequisite to starting Paddle verification. The affected scope may include
  re-ingestion, database/index copies, R2 content, logos, caches and datasets.
  Public wording provides a request channel without claiming an already tested
  end-to-end takedown guarantee.
- The old blanket fair-use/fair-dealing explanation has been removed from the
  proposed Terms. This corrects the assertion, not the unresolved legal basis.

## Questions for the reviewer

1. Which law/jurisdiction governs each relevant act: Swiss operation, foreign
   employer/platform content, public access, US model processing and redistribution?
2. For which content and uses is permission unnecessary, available through an
   actual licence, or supportable under a specific exception? Evaluate commercial
   text/data mining, temporary copying and database rights separately. Do not
   assume US fair use or Swiss research exceptions apply across the service.
3. What exclusions, source permissions, quotation/excerpt limits, attribution,
   update/removal controls or contract changes are needed for unsupported uses?
4. Does TypeSafe's applicable customer agreement permit our provision of inputs
   under the identified basis? Separate supplier input warranties from copyright
   exceptions and retention/data-protection questions.
5. Is the proposed licence scope accurate, and do any existing dataset releases
   or notices require correction? How should a substantiated complaint be handled?
6. What short, accurate rights summary can be given to Paddle without disclosing
   privileged advice or overstating coverage?

Please return a source/use matrix recording **permitted / permitted subject to
conditions / unresolved / exclude**, its legal basis, evidence and next action.
Keep confidential correspondence, contracts and identity documents private. The
assessment uses public sources and non-sensitive implementation evidence;
resulting work is tracked in #10079.

## Reference frame

The [Swiss IPI guidance](https://www.ige.ch/en/protecting-your-ip/copyright/using-a-work)
and its [exceptions overview](https://www.ige.ch/en/protecting-your-ip/copyright/using-a-work/permitted-uses)
explain permission and specific exceptions; this brief does not infer that every
posting is protected or infringed. [Paddle's AUP](https://www.paddle.com/help/start/intro-to-paddle/what-am-i-not-allowed-to-sell-on-paddle)
also raises IP/platform-terms questions. The owner has sent the separate category
inquiry (#10078); category acceptance would not itself establish content rights.
