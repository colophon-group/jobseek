# Production Go sitemap monitor

The `boundedhttp` and `sitemap` packages are a snapshot of the admitted
`pilots/go-http-sitemap` implementation. They live in this module because the
crawler image builds from `apps/crawler/` as its Docker context. The production
command permits only a cached HTTPS `urlset`, at most three root requests, and
50,000 URLs. The Python adapter applies the existing URL filters and transforms
before passing the result to the existing board writer.

`SITEMAP_GO_BOARD_IDS` is default-off and selects exact board IDs. Before
adding an ID, compare the board's natural Python output with the bounded Go
runner and confirm its cached sitemap is a `urlset` with no proxy, SSL override,
or alternate XML retry setting. The selected monitor makes the normal single
origin request; no shadow request is sent during that cycle.

Changes to either copied package should be reconciled with the pilot module's
security and parity tests. The local tests cover the production `urlset` and
accounting contracts.

The ordinary Go worker also calls this parser directly for
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
