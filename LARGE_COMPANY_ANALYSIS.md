# Large Company Issue Analysis

Analysis of open issues at https://github.com/colophon-group/jobseek/issues that involve large companies whose job posting volume may require extra care to avoid excessive resource consumption.

## High-Risk Companies

### Tier 1 — Massive (likely 5,000-30,000+ global openings)

| Issue | Company | Est. Employees | Risk |
|-------|---------|---------------|------|
| #205 | **PwC** | ~328,000 | Big 4 firm. Similar to KPMG which already needs **14 separate boards** across countries. Would likely need the same multi-board approach. |
| #196 | **Infosys** | ~300,000 | IT services giant. Enormous hiring volume, likely 10,000+ open roles globally. |
| #149 | **Johnson & Johnson** | ~130,000 | Pharma/consumer giant. Thousands of global openings. |
| #156 | **Novartis** | ~100,000 | Pharma giant, HQ in Basel. Likely 3,000-8,000+ openings. |
| #155 | **Roche** | ~100,000 | Pharma giant, also Basel-based. Similar volume to Novartis. |
| #195 | **Oracle** | ~160,000 | Enterprise tech. Likely 5,000-10,000+ openings on Workday/custom ATS. |
| #192 | **Cisco** | ~80,000 | Networking giant. Likely 3,000-5,000+ openings. |

### Tier 2 — Large (likely 1,000-3,000 global openings)

| Issue | Company | Est. Employees | Risk |
|-------|---------|---------------|------|
| #147 | **Adobe** | ~30,000 | 1,000-2,000+ openings. Uses Workday — pagination could be expensive. |
| #153 | **Thomson Reuters** | ~25,000 | 1,000-2,000+ openings. |
| #141 | **Logitech** | ~7,000 | Smaller but still 300-500+ openings, HQ in Switzerland. |
| #151 | **On** (On Running) | ~3,000 | Growing fast, 200-500+ openings. Swiss HQ. |

## Why These Are Dangerous

Existing configurations for comparison:
- **Google** already has `"urls": 3984` in its sitemap config — nearly 4,000 URLs to crawl
- **KPMG** required **14 separate board configs** across countries (CH, DE, FR, IT, UK, US, AU, CA, NZ, etc.)
- **Amazon** needed a **custom monitor type** (`amazon`) entirely

For the Tier 1 companies:
1. **Scraping all global postings** without URL/location filtering would mean downloading and parsing 5,000-30,000 pages per crawl cycle.
2. **Scraper cost**: each page needs HTTP requests (some with Playwright rendering), so a misconfigured 20,000-posting company could dominate batch processing time and resources.
3. **PwC** specifically would likely need the same multi-country board approach as KPMG — multiplying the configuration complexity.
4. **Pharma companies** (Novartis, Roche, J&J) often use SAP SuccessFactors or Workday with complex pagination that can time out on large result sets.

## Recommendations

- **PwC, Infosys, Oracle, Cisco, J&J, Novartis, Roche**: Consider scoping to Switzerland-only (or a specific region) first, using URL filters or API parameters to limit results. These should not be added with a "scrape everything globally" approach.
- **Adobe, Thomson Reuters**: Manageable if proper pagination limits are set, but still worth adding with `max_pages` or count guards.
- **Logitech, On**: Reasonable size, lower risk, but still verify posting counts before configuring.

The safest pattern from the existing codebase is what KPMG does — separate boards per country with independent configs — but that's labor-intensive. At minimum, the Tier 1 companies should have explicit count caps or geographic filtering in their monitor config.
