---
type: case-study
company: ugi-corporation
monitor: sitemap
scraper: dom
summary: "UGI worldwide coverage requires six distinct sources: SuccessFactors feed324 exactly matches live US count; French7 use filtered sitemap plus rendered DOM with explicit cover-title wait after API tests lacked locations; UK3 static DOM; Belgium3 mixed HTML/PDF through document_fallback; DK/NO one each from official sitemaps and current application evidence. Exclude expired Finnish jobs, empty NL/LU/SE sources and divested European brands. No URL overlap. Local parser binary absence was verified using the existing offline oracle selector only; production configs contain no override."
tags: ['successfactors', 'sitemap', 'dom', 'pdf', 'multiboard', 'spa', 'currentness']
---
# Ugi-Corporation — UGI worldwide coverage requires six distinct sources: SuccessFactors feed324 exactly matches live US count; French7 use filtered sitemap plus rendered DOM with explicit cover-title wait after API tests lacked locations; UK3 static DOM; Belgium3 mixed HTML/PDF through document_fallback; DK/NO one each from official sitemaps and current application evidence. Exclude expired Finnish jobs, empty NL/LU/SE sources and divested European brands. No URL overlap. Local parser binary absence was verified using the existing offline oracle selector only; production configs contain no override.

## Setup
- Monitor: sitemap
- Scraper: dom

## Key decisions
<!-- What was non-obvious? What was tried? What worked? -->

## Config
<!-- Final scraper_config / monitor_config JSON -->
