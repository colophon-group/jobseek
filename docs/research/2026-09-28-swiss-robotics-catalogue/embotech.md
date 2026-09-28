# Embotech catalogue evidence — 28 September 2026

## Company and current board

The [canonical site](https://embotech.ai/) has moved from `embotech.com`.
The old `/company/careers/` URL redirects to the new domain but returns 404.
Its cached search result lists obsolete positions and was discarded.

The [current official careers board](https://embotech.ai/career) links three
PDF descriptions, confirmed through direct live HTTP and the configured DOM
monitor. A cached web-search rendering showed only two; the live third role
was included.

| Role | Location printed in the PDF |
| --- | --- |
| Software Team Lead – Safety Software | Zurich, Switzerland |
| System Engineer (Mid/Senior) | Munich, Germany or Zurich, Switzerland |
| System Operator (Autonomous Trucks) | Maasvlakte 2, Rotterdam, The Netherlands |

The [official company overview](https://embotech.ai/about-us) identifies an ETH
Zurich spin-off founded in 2013 and a team of about 140 people. Employee bucket
3 (51–200) and industry Robotics (20) reflect these facts. Embotech develops
autonomous driving systems for industrial logistics, including factory vehicle
movements and terminal tractors. Four locale descriptions use those facts.

## Configuration and verification

Static `dom` monitoring selects PDF links on the official Webflow project CDN.
The `pdf` scraper anchors title and location extraction after the careers email
in the document header. This skips the employer's Zurich contact address and
extracts the role's actual workplace. The final pattern preserves the entire
location line, including both Munich and Zurich for the dual-location role.
An initial pattern that returned only the first location was replaced and all
three PDFs were retested.

Final results: 3/3 titles, full descriptions and location strings. All three
descriptions were read completely and contain duties, requirements and
application instructions. PDF text has minor original spacing artefacts but
retains substantive content. Employment mode and dates absent from the PDFs
were not inferred from filenames. No separate current ATS or regional board
was found; the worldwide board includes the Netherlands role.

Local evidence retained in the setup workspace:

- `apps/crawler/.workspace/embotech/artifacts/careers/monitor/run-20260928T133105/`
- `apps/crawler/.workspace/embotech/artifacts/careers/scraper/run-20260928T133230/`

Feedback verdict: `good`. Local `ws submit` passed quality gates and CSV
validation with `WS_LOCAL=1`; no deployment, database, commit or PR mutation
was performed by this setup run.

## Artwork

The official vectors were visually reviewed as a wordmark plus mark and a
distinct compact mark:

- [White wordmark and mark](https://cdn.prod.website-files.com/69dc93b3821010806c215f9b/69dc93b3821010806c215fbf_embotech-logo_weiss.svg)
- [Compact red mark](https://cdn.prod.website-files.com/69dc93b3821010806c215f9b/69dc93b3821010806c216047_Webclip.svg)

The standard workspace pipeline staged the selected SVG artwork in
`apps/crawler/data/images/embotech/`. R2 publication and registry URL updates
remain the normal image-upload workflow step.
