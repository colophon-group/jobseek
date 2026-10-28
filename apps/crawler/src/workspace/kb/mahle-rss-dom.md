---
type: case-study
company: mahle
monitor: rss
scraper: dom
summary: "MAHLE global SuccessFactors feed and sitemap expose 540 multilingual URLs while live search totals are locale-specific (en_US 310, de_DE 111, ja_JP 10). Exclude three Careers InMotion talent pools to retain 537 roles. Regional official landing pages share this global source, but Japan graduates link a distinct Mynavi tenant with 5 open and 3 closed courses; select .courseList .box02:not(.courseClosed), use rich-row titles/prefectures and static DOM description enrichment through requirements, salaries and benefits, stopping before contact. JSON-LD absent; rendered listing adds cost without coverage. Boards have zero overlap. DOM verification used repository-supported local Python parser because resolver host lacks native executable; no override persisted."
tags: ['successfactors', 'multilingual', 'mynavi', 'japan', 'closed-courses', 'rich-enrichment']
---
# Mahle — MAHLE global SuccessFactors feed and sitemap expose 540 multilingual URLs while live search totals are locale-specific (en_US 310, de_DE 111, ja_JP 10). Exclude three Careers InMotion talent pools to retain 537 roles. Regional official landing pages share this global source, but Japan graduates link a distinct Mynavi tenant with 5 open and 3 closed courses; select .courseList .box02:not(.courseClosed), use rich-row titles/prefectures and static DOM description enrichment through requirements, salaries and benefits, stopping before contact. JSON-LD absent; rendered listing adds cost without coverage. Boards have zero overlap. DOM verification used repository-supported local Python parser because resolver host lacks native executable; no override persisted.

## Setup
- Monitor: rss
- Scraper: dom

## Key decisions
<!-- What was non-obvious? What was tried? What worked? -->

## Config
<!-- Final scraper_config / monitor_config JSON -->
