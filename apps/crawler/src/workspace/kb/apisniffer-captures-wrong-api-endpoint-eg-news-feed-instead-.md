---
step: select_monitor
symptom: api_sniffer captures wrong API endpoint (e.g., news feed instead of jobs API) because the page navigates away during show-more click or the jobs API is called from a separate domain (api.example.com) not captured by auto-detection
tags: ['api_sniffer', 'wrong-endpoint', 'js-bundle', 'manual-api-discovery', 'wordpress']
---
# api_sniffer captures wrong API endpoint (e.g., news feed instead of jobs API) because the page navigates away during show-more click or the jobs API is called from a separate domain (api.example.com) not captured by auto-detection

## Problem
api_sniffer captures wrong API endpoint (e.g., news feed instead of jobs API) because the page navigates away during show-more click or the jobs API is called from a separate domain (api.example.com) not captured by auto-detection

## Solution
Inspect JS bundles loaded by the careers page to find the actual API endpoint. Look in webpack chunks for fetch/axios calls, URL patterns, or base URL configs. The job search API may be on a separate subdomain (e.g., api.lifeatspotify.com) or use a non-standard path (e.g., WordPress REST API /wp-json/). Configure api_sniffer with the discovered api_url directly instead of relying on auto-detection.
