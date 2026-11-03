# Production Go sitemap monitor

The `boundedhttp` and `sitemap` packages are the production implementation.
The standalone command permits a cached HTTPS `urlset`, at most three root
requests, and 50,000 URLs. Workspace tooling can use its default-off exact-board
adapter; the ordinary Go worker calls the parser directly.

The ordinary Go worker calls this parser directly for
`sitemap.explicit-urls/v1`. Its compiled configuration traverses up to eight
index levels and 200 distinct same-origin HTTPS children, with three attempts
for transient child failures. It resolves the selected shards before publishing
an inventory; an exhausted required child fails the cycle and preserves prior
postings. Cycles are skipped, job-related children take precedence, legitimate
missing or malformed child documents are skipped, and a valid empty URL set
retains the existing empty-inventory confirmation. Publisher reservations stop
traversal and bind the actual child resource. The sealed worker transport keeps
the existing 50 MiB per-document and 55 MiB aggregate body limits. Cross-origin
children, discovery, proxy routes and configured XML retry overrides still need
their replacement contracts. Standalone command defaults remain URL-set-only.
