---
step: configure
symptom: Foundever inventory SuccessFactors legacy board redirected to jobs.foundever.com while seeded RSS guessed career4.successfactors.com/googlefeed.xml and returned404; global country selector missed active Brazil openings.
tags: ['successfactors', 'rss', 'redirect', 'pandape', 'brazil', 'foundever']
---
# Foundever inventory SuccessFactors legacy board redirected to jobs.foundever.com while seeded RSS guessed career4.successfactors.com/googlefeed.xml and returned404; global country selector missed active Brazil openings.

## Problem
Foundever inventory SuccessFactors legacy board redirected to jobs.foundever.com while seeded RSS guessed career4.successfactors.com/googlefeed.xml and returned404; global country selector missed active Brazil openings.

## Solution
Re-probe the redirecting tenant and explicitly configure rss preset successfactors feed_url https://jobs.foundever.com/googlefeed.xml (428 jobs). Check separate regional ATS despite centralized selectors: foundever.pandape.infojobs.com.br exposes77 Brazil jobs vs zero on global Brazil category. Use computrabajo monitor and json-ld scraper; compare boards to confirm zero URL overlap.
