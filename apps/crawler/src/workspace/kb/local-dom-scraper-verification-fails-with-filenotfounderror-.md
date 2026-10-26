---
step: scraper
symptom: Local DOM scraper verification fails with FileNotFoundError for /usr/local/bin/dom-detail-parse although static HTML and a valid DOM config are present.
tags: ['dom', 'local-verification', 'missing-binary', 'vinfen']
---
# Local DOM scraper verification fails with FileNotFoundError for /usr/local/bin/dom-detail-parse although static HTML and a valid DOM config are present.

## Problem
Local DOM scraper verification fails with FileNotFoundError for /usr/local/bin/dom-detail-parse although static HTML and a valid DOM config are present.

## Solution
Distinguish a missing resolver-host executable from extraction failure. The existing runtime supports explicit DOM_GO_PARSE_ENABLED=0 for local Python-parser verification; confirm that supported selector in repository-owned runtime documentation when authorized, run the same named config locally, inspect full description and field semantics, and never persist the environment override in board JSON. Production uses the normally installed parser. Vinfen Gateway Arts verified 1/1 required fields without any runtime code change.
