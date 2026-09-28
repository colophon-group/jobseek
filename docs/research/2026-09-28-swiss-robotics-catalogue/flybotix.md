# Flybotix catalogue evidence — 28 September 2026

## Company and board

- [Official careers page](https://www.flybotix.com/careers/) links the unfiltered [Personio board](https://flybotix.jobs.personio.com).
- [Official company overview](https://www.flybotix.com/about-us/) describes ASIO inspection drones and associated software.
- [Official financing announcement](https://www.flybotix.com/flybotix-secures-close-to-usd-10m-in-series-a-extension-financing-confined-space-inspection-drone/) confirms founding in 2019.
- The [company's LinkedIn profile](https://www.linkedin.com/company/flybotix/) reports 11–50 employees, mapped to employee bucket 2. Industry is Robotics (20).

The actual ATS lists four postings: Project Management Lead; Robotics Software
Engineer – Ground Control & Onboard Systems; Technical Customer Support
Engineer; and Spontaneous Application. The last is a talent-pool invitation,
not a specific vacancy. The marketing careers page still advertises a
Marketing Internship that is absent from the ATS; it is not included.

## Configuration and verification

`personio` with `{"slug":"flybotix"}` discovers all four canonical detail URLs.
The feed contains full descriptions but labels every office `HQ - CH` and
carries an old talent-pool date. Therefore the board explicitly uses the
`json-ld` detail scraper instead of skipping scraping.

All four detail pages were tested successfully: 4/4 titles, substantive HTML
descriptions, locations (`Renens, CH`), employment types and posting dates.
For example, the [robotics software role](https://flybotix.jobs.personio.com/job/2668687)
publishes the structured address Rue de Lausanne 64, 1020 Renens, CH.
The project-management, robotics-software and customer-support descriptions
were read fully and checked for duties and requirements. No separate regional
board was found; the complete worldwide ATS is configured.

Local evidence retained in the originating setup workspace:

- `apps/crawler/.workspace/flybotix/artifacts/careers/monitor/run-20260928T132430/`
- `apps/crawler/.workspace/flybotix/artifacts/careers/scraper/run-20260928T132542/`

Feedback verdict: `good`. Local `ws submit` passed quality gates and CSV
validation with `WS_LOCAL=1`; it did not commit, push or create a PR.

## Artwork

Both original transparent assets were visually reviewed against the official
site. The higher-resolution bird mark was selected instead of a small favicon:

- [Full wordmark and bird, 1501×370](https://www.flybotix.com/wp-content/uploads/2024/02/logo2-8.png)
- [Distinct bird mark, 543×674](https://www.flybotix.com/wp-content/uploads/2024/03/Flybotix-Logo-bird-transparent-background-2.png)

Staged originals are `apps/crawler/data/images/flybotix/logo.png` and
`icon.png`. The standard image workflow publishes R2 assets and fills the
registry logo/icon URLs.
