---
type: case-study
company: citigroup
monitor: sitemap
scraper: json-ld
summary: "Eightfold AI 2k offset cap; pivoted from DOM to sitemap on subdomain"
tags: [eightfold, sitemap, json-ld, offset-cap, pivot]
---
# Citigroup — Pivoting from Eightfold AI to sitemap after offset cap discovery

## Setup
- Monitor: sitemap (XML sitemap at jobs.citi.com indexing all ~7,200 job URLs)
- Scraper: json-ld (standard JobPosting schema on job detail pages)

## Key decisions
- Citigroup's main careers page uses Eightfold AI (citi.eightfold.ai) — the agent tried 20+
  DOM and API configurations attempting to paginate through all jobs
- Eightfold AI imposes a hard 2,000 offset cap: any request with offset > 2000 returns an
  empty result set, making it impossible to retrieve all ~7,200 jobs
- DOM monitor with rendered pagination hit the same cap — Eightfold's frontend lazy-loads
  results and refuses to scroll beyond position 2000
- api_sniffer captured the Eightfold search API but the same offset limit applied server-side
- Solution: discovered a separate subdomain (jobs.citi.com) hosting a sitemap.xml that indexes
  all job URLs without any offset restrictions
- The sitemap URLs point to job detail pages that contain standard JSON-LD JobPosting markup,
  making json-ld scraper the natural choice
- Abandoned the Eightfold API entirely rather than accepting partial coverage (~28% of jobs)

## Config
```json
{
  "monitor_config": {
    "sitemap_url": "https://jobs.citi.com/sitemap.xml",
    "job_url_pattern": "jobs\\.citi\\.com/job/"
  },
  "scraper_config": {}
}
```

## Lesson
When an ATS platform imposes hard pagination or offset caps (common with Eightfold AI at 2k,
some Phenom People at 1k), don't keep trying different pagination strategies on the same
endpoint — the cap is server-side. Instead, look for alternative entry points: sitemaps on
subdomains, Google feed URLs, or RSS feeds. Companies with large job counts often maintain
a sitemap for SEO purposes that bypasses the ATS frontend entirely. Check `robots.txt` and
common sitemap paths on related subdomains (jobs.*, careers.*).
