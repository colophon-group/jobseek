---
type: case-study
company: kpmg
monitor: mixed
scraper: mixed
summary: "Multi-region company with 14 boards spanning 7 different monitor types"
tags: [multi-region, multi-board, mixed-monitors, rss, successfactors, lever, smartrecruiters, dom, api_sniffer]
---
# KPMG — Multi-region setup with 14 boards across 7 monitor types

## Setup
- 14 boards across different regional career sites
- Monitor types used: rss (6), dom (2), api_sniffer (3), lever (1), smartrecruiters (1), sitemap (0), greenhouse (0)
- Scraper types: skip (rich monitors), json-ld (2), dom (1), none (where monitor provides all data)

## Key decisions
- Each KPMG region runs a different ATS platform — no single monitor type covers all
- SuccessFactors regions (AT, DE, ES, IT, LU, ME, SG) use RSS with `preset: "successfactors"`
  and Google feed XML URLs (`/googlefeed.xml`)
- Switzerland (`jobs.kpmg.ch`) uses a Prospective.ch API — api_sniffer with offset pagination
- US uses a WordPress-based site with api_sniffer + json-ld fallback to DOM (render + networkidle)
- Canada uses api_sniffer with page-style pagination
- UK uses DOM monitor + DOM scraper with regex-based field extraction
- France uses DOM with pagination param + json-ld scraper
- New Zealand uses Lever with standard token
- Australia uses SmartRecruiters with standard token

## Board breakdown
| Region | Board slug      | Monitor           | Scraper  |
|--------|-----------------|-------------------|----------|
| AT     | kpmg-at         | rss (SF)          | skip     |
| AU     | kpmg-au         | smartrecruiters   | skip     |
| CA     | kpmg-ca         | api_sniffer       | skip     |
| CH     | kpmg-careers    | api_sniffer       | skip     |
| DE     | kpmg-de         | rss (SF)          | skip     |
| ES     | kpmg-es         | rss (SF)          | skip     |
| FR     | kpmg-fr         | dom               | json-ld  |
| IT     | kpmg-it         | rss (SF)          | skip     |
| LU     | kpmg-lu         | rss (SF)          | skip     |
| ME     | kpmg-me         | rss (SF)          | skip     |
| NZ     | kpmg-nz         | lever             | skip     |
| SG     | kpmg-sg         | rss (SF)          | skip     |
| UK     | kpmg-uk         | dom               | dom      |
| US     | kpmg-us         | api_sniffer       | json-ld  |

## Lesson
Large multi-region companies often require a different monitor+scraper combination per
region. Don't assume one approach covers all boards. Start with `ws probe monitor` on each
board independently — the same company may use SuccessFactors in Europe, Lever in APAC,
and a custom WordPress site in the US. Rich monitors (rss with successfactors preset,
lever, smartrecruiters) can skip the scraper entirely.
