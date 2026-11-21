---
step: configure
symptom: Magellan Health Workday list API returns HTTP 303 after retries; Phenom JSON-LD omits locations for some roles and embedded location coverage counts empty strings as present.
tags: ['phenom', 'workday', 'embedded', 'sitemap', 'locations', 'quality']
---
# Magellan Health Workday list API returns HTTP 303 after retries; Phenom JSON-LD omits locations for some roles and embedded location coverage counts empty strings as present.

## Problem
Magellan Health Workday list API returns HTTP 303 after retries; Phenom JSON-LD omits locations for some roles and embedded location coverage counts empty strings as present.

## Solution
Use the official Phenom sitemap filtered to /us/en/job/ after comparing against the unfiltered live total (417 matched). Static embedded phApp.ddo jobDetail.data.job preserves full HTML and multi_location[].cityStateCountry. Inspect actual values rather than coverage counts: Poland is stated only in its description; Wisconsin has a storeLocation string. Keep any exception narrowly bound to verified source evidence. Do not trust ml_country (Poland mislabeled Nepal) or map Not Remote to onsite when explicit Remote titles contradict it.
