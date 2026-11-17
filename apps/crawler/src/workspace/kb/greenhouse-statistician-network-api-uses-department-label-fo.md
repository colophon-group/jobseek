---
step: select_monitor
symptom: Greenhouse Statistician Network API uses department label for every location and entity-escapes description HTML
tags: ['greenhouse', 'api_sniffer', 'locations', 'html-unescape', 'enrichment']
---
# Greenhouse Statistician Network API uses department label for every location and entity-escapes description HTML

## Problem
Greenhouse Statistician Network API uses department label for every location and entity-escapes description HTML

## Solution
For Genius Sports Statistician Network, all 350 location.name and offices locations were Statistician Network while all titles carried a real place after the final separator. Existing api_sniffer direct HTTP replay with explicit title, html_unescape description, first_published date and department/requisition mappings preserved full data. A static DOM scraper with enrich locations and H1 regex recovered 10 of 10 diverse sample locations; all 350 titles were checked for suffix coverage. Native Greenhouse profile admits skip only, so do not attach an unsupported enrichment route to it. Existing API rich plus DOM route required no runtime changes. Local missing dom-detail-parse was handled only with the reference selector explicitly documented in go/dom-detail/README.md, never persisted in board configuration.
