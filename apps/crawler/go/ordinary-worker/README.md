# Native ordinary processing assembly

This package connects the standard Greenhouse token/skip inventory to the
existing native enrichment, owned rich batches and terminal board lifecycle.
It is an assembly library; the native process executable and HTTP/circuit
integration are still required before a production owner can be selected.

`NormalizeGreenhouseInventory` matches the default Python non-streaming rich
monitor's raw-URL dictionary, URL sanity/canonicalization and canonical alias
content rules. Raw duplicate URLs retain their first dictionary position and
last content. Canonical aliases then retain the last raw dictionary entry's
content. Jobs are sorted only after those decisions, before native chunks.
Truncation preserves every collected job and suppresses absence through the
terminal cycle. No provider identity or alternate/filter/proxy profile is
introduced.

`NativeRichPreparer` reuses `Processor.PrepareRichMonitor`, including its model,
immutable taxonomy/currency snapshot and serialized native location index.
`PersistGreenhouseInventory` validates inventory accounting, prepares each whole
500-row chunk outside the PostgreSQL transaction, writes only prepared chunks
through the opaque owned cycle, and finalizes only the complete processing run.
Every failed/canceled preparation or persistence path invalidates later
success/absence authority; earlier committed chunks survive for failure or
lease recovery. Terminal deadlines still come from the database receipt.

The ordinary queue workflow requires PostgreSQL17 and private Redis on both
Linux architectures. It regenerates 40 URL and six inventory cases from the
actual Python functions, rejecting stale captures, and exercises inventories
above the 50,000-job flag without slicing. Real native assembly proof loads
reference tables and the SQLite location index in an owned schema, processes
1,001 postings across three batches, checks enrichment/description/R2/absence
effects and settles the exact canonical deadline. A separate preparation failure
after the first committed 500 rows proves that no partial second chunk or
success/absence receipt survives, while canonical failure scheduling remains
available.

Continue with a persistent verified HTTP client and typed response outcomes,
matching redirect/SSRF/header/cookie/request accounting and Greenhouse's single
GET behavior. Integrate shared host circuits, deferrals, exact-source startup,
heartbeat and bounded shutdown into the native executable. Prove the installed
process and supported all-writer cutover/cold reversal with B0's shared epoch
before selection. The [continuation plan](../../../../docs/27-go-lightpanda-continuation-plan.md)
retains all enabled profiles, remaining runtime consumers, whole-service cost,
the rollback window and production Python/Playwright/Chromium retirement.
