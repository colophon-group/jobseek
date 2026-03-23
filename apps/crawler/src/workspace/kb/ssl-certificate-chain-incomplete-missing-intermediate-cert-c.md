---
step: select_monitor
symptom: SSL certificate chain incomplete (missing intermediate cert) causes all HTTP-based monitors (sitemap, RSS, API) to fail with SSL: CERTIFICATE_VERIFY_FAILED
tags: ['ssl', 'certificate', 'dom', 'render', 'sitemap', 'rss']
---
# SSL certificate chain incomplete (missing intermediate cert) causes all HTTP-based monitors (sitemap, RSS, API) to fail with SSL: CERTIFICATE_VERIFY_FAILED

## Problem
SSL certificate chain incomplete (missing intermediate cert) causes all HTTP-based monitors (sitemap, RSS, API) to fail with SSL: CERTIFICATE_VERIFY_FAILED

## Solution
Use DOM monitor with render:true - Playwright/Chromium handles incomplete SSL chains gracefully unlike Python's httpx/requests. Alternative: build complete cert chain file and set REQUESTS_CA_BUNDLE env var.
