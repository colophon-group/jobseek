# Swiss robotics catalogue verification: Zurich and Swisslog

Verified on 28 September 2026. Changes are configuration and catalogue data only.

## Swisslog

- **Employer:** Swisslog, warehouse automation and material-handling systems; Swiss headquarters in Buchs AG, near Aarau. Its [official locations description](https://www.swisslog.com/de-de/karriere/swisslog-als-arbeitgeber/attraktive-arbeitsstandorte-im-deutschsprachigen-raum) identifies the Swiss operation and KUKA ownership. The configured careers source is Swisslog's own portal, not a shared parent-company board.
- **Board:** [Official vacancies](https://www.swisslog.com/en-us/careers/openings), canonical company slug `swisslog` and board slug `swisslog-careers`.
- **Discovery:** `api_sniffer` replays the public API referenced by the page's `div#mod-joblist` `data-url`: `https://www.swisslog.com/en-us/api/job/getjobs?searchrootpath=24F53394AB4E44F1A28C930664FDC228`. The `items` array supplies `href`, `headline` and `facetsTop`.
- **Completeness:** the API and configured monitor returned **53 jobs**, matching the rendered page's **53 Results**. Five had Swiss locations. The page initially renders ten rows; the API response contains the whole inventory. An initial probe used an estimate of 98; the subsequent rendered/API count supersedes that estimate.
- **Detail extraction:** retain the explicit `json-ld` scraper. The monitor supplies titles and locations but no descriptions; `skip` would be incorrect. Verified samples:
  - [Software Project Manager (m/w/d)](https://www.swisslog.com/de-de/karriere/offene-stellen/software-project-manager-mwd-4385): Buchs, Schweiz; complete responsibilities and requirements.
  - [Cyber Security Incident Responder](https://www.swisslog.com/en-us/careers/openings/cyber-security-incident-responder-4307): Petaling Jaya, Malaysia; complete responsibilities and requirements.
- Both samples also yielded employment type, publication date and expiry from source JSON-LD. The source includes the employer suffix in its job title; it is preserved.
- Added factual EN/DE/FR/IT descriptions. Official [wordmark](https://www.swisslog.com/-/media/swisslog/images/system/swisslog-logo-small.png) and [compact signet](https://www.swisslog.com/-/media/swisslog/images/system/swisslog-signet.svg) were visually reviewed and staged under `apps/crawler/data/images/swisslog/`. Asset URLs are populated by the normal image-upload workflow.

## roboa

- **Employer/product:** [roboa](https://www.roboa.ch/) builds robots for inspection, repair and cable installation in confined spaces.
- **Migration:** preserve canonical company `roboa` and board `roboa-careers`; replace the legacy Google Sites URL with [the current official careers page](https://www.roboa.ch/career).
- **Discovery:** static `dom` monitor follows `/career/jobs/` links and excludes `speculative-application`.
- **Completeness:** the current page has **three concrete vacancies and one speculative application**. The configured monitor returns all three vacancies:
  - [Field & Hardware Engineer (60%-100%)](https://www.roboa.ch/career/jobs/field-hardware-engineer): Zurich, Switzerland.
  - [Service and Production Technician (60–100%)](https://www.roboa.ch/career/jobs/service-und-produktionstechniker): Zürich.
  - [Software Intern (6 Months)](https://www.roboa.ch/career/jobs/software-intern): Zürich.
- **Detail extraction:** static `dom` scraper extracts the `h1` title, location and on-site work mode from the introductory `h4`, and the description through responsibilities, requirements and benefits. It stops before `How to Apply`, excluding the application form. All three samples yielded correct titles, locations, descriptions and work mode. Publication dates and employment-type fields were not invented.
- Added missing EN/DE/FR/IT company descriptions. Local sample verification used `DOM_GO_PARSE_ENABLED=0` because the host lacks `/usr/local/bin/dom-detail-parse`; the production configuration contains no parser override.

## Verity and identity checks

- Corrected the existing `verity` company's homepage from `https://veritystudios.com/` to [https://www.verity.net/](https://www.verity.net/).
- The already configured [official Breezy board](https://verity-ag.breezy.hr/) links the current homepage and lists Zurich machine-vision, autonomy/perception and technical-lead roles. No duplicate company or board was created.
- [Sevensense's official company page](https://www.sevensense.ai/company) explicitly identifies it as an ABB business unit, with Zurich operations and a job link to ABB careers. Use the existing canonical `abb` company for Sevensense coverage; do not create a separate independent-employer record.

## Validation

`WS_LOCAL=1` submissions for Swisslog and roboa passed board quality gates and global CSV validation. `git diff --check` passed. Local mode performed no commit or push. An earlier global validation failure caused by a concurrently edited empty-name stub cleared after that worker completed its metadata; neither submission used `--force`.

Counts and vacancies are verification snapshots, not promises of continuing availability. Production ingestion and public catalogue visibility must be checked after the normal deployment.

## roboa HTTP compatibility follow-up

The first GitHub board probe failed with HTTP 403 on all three attempts. A
read-only comparison from the production crawler host reproduced the listing
and detail failures with the default HTTP client headers. The same URLs returned
HTTP 200 with a browser User-Agent. Testing headers separately established that
**User-Agent alone** was sufficient; adding only `Accept` or only
`Accept-Language` still returned 403. The bare hostname redirects to `www`, so a
hostname change would not resolve the cause.

Both the static monitor and detail scraper now set the allowlisted public
`request_headers.User-Agent`. The corrected requests returned the complete
listing and all three detail pages from the production host. Repeated `ws`
verification still found three vacancies and extracted all three titles,
locations, descriptions and on-site work modes. No browser or proxy mode was
enabled, and the CI static board probe remains active. The upstream sitemap was
also inspected but contained different, stale career links, so it was not used
as a replacement for the current careers listing.
