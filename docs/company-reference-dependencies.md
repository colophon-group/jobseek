# Company reference dependency inventory

Ownership contract: [ADR 008](adr/008-company-reference-ownership.md).
Delivery: #10222; inventory #10223; schema #10224; writers #10225; readers #10226;
CI/canaries #10227; promotion/legacy retirement #10228.

Paths below are repository-relative. This inventory distinguishes executable
runtime dependencies from retained mirror schema and historical test evidence.

## Executable lifecycle inventory

[`company-reference-dependencies.json`](../apps/web/scripts/company-reference-dependencies.json)
is the machine-readable database lifecycle manifest. Each inbound company or
reference FK declares its owner, active-selection versus retained-history
classification, producer, replacement producer, and bridge/final disposition.
The deployed inventory contains six FKs. A read-only catalog probe on
2026-10-03 confirmed that the runtime/0075 Murmur table is absent from production.
That known legacy dependency is inventoried as optional retained history: only
its exact six-column/default/PK shape and SET NULL company FK are accepted if
present. Malformed optional history and all unknown references fail verification. The checker refuses retiring the legacy compatibility producer while active
selections still require legacy rows; final selections require restrictive web
references and a live replacement materializer. Retained inert/history rows need
an explicit owner and independent retirement disposition. Saved jobs declare a
self-contained company snapshot lifecycle with no company-identity FK.

[`company-reference-dependency-check.ts`](../apps/web/scripts/company-reference-dependency-check.ts)
compares **all** inbound FKs to `public.company` and `public.company_reference`
against that manifest, including columns, target columns, actions, validation and
immediacy. It also verifies saved-job company snapshot types/nullability and the
absence of any FK on its company identity. Unknown, missing or weakened
relationships fail closed with metadata-only diagnostics. It runs inside the
read-only pre/post/drift audit used by protected migration and scheduled drift
workflows. The expansion calls bridge mode; the separate final-contract audit
must call reference mode after its exact migration ledger proves cutover.

Changing a producer's lifecycle requires updating its declared retirement phase
and every retained dependency's replacement/disposition in the same reviewed
change. Required CI generates the current Drizzle schema, proves its optional historical
Murmur shape, and separately proves the deployed variant without that table and both
phase inventories, unexpected legacy/reference FKs, missing historical relations,
and independent snapshots. Behavioral service/browser tests still prove the
replacement writer works; a declaration or source-text check alone is not enough.

## Database relationships

| Dependency | Classification and owner | Migration target and verification |
| --- | --- | --- |
| `watchlist_company.company_id → company.id`, cascade | Active; web selections (#10225/#10228) | Bridge materialization, then 0101 reference FK with RESTRICT. Real PG mutation/retirement tests; preserve membership IDs, timestamps, ownership and filters. |
| `followed_company.company_id → company.id`, cascade | Active; web stars (#10225/#10228) | 0101 restrictive reference target. PG concurrent toggle, removal/outage and retention tests. |
| `company_description.company_id → company.id`, cascade | Inert mirror compatibility; catalogue owners | Keep during bridge. No runtime reader/writer remains; separately audit and retire, without reactivating mirror sync. |
| `job_board.company_id → company.id`, cascade | Inert mirror compatibility; catalogue owners | Keep, independently retire after auditing `company_request.resolved_job_board_id`. Catalogue/search use crawler/Typesense. |
| `hiring_signal.company_id → company.id`, cascade | Inert feature schema; web feature owner | Keep; no runtime producer/consumer. `outreach_draft` references signals and must enter its eventual retirement audit. |
| `company_request.resolved_company_id → company.id`, set null | Compatibility resolution fields; company-request owner | Keep resolution FKs; active request submission only records request/issue metadata. Do not activate a resolver; audit historical records before retiring resolution fields. |
| `murmur_accept_log.company_id → company.id`, set null (optional historical table; absent in production) | Feature-gated legacy status reader; Murmur owner | Preserve its consumer inventory. No active accept/webhook catalogue writer exists; the audit recognizes only its exact historical table/FK shape if retained elsewhere. Feature retirement must account for the status endpoint and its tests. |
| `saved_job.company_id` (no company FK) | Active independent durable history; saved-jobs owner | No cutover. Preserve `company_name`, `company_slug`, `company_icon`, posting snapshots and interview relations. Saved-job snapshot tests/PG preservation assertion. |
| `company_reference.id` | Active web reference owner (#10224) | Exact schema/check/provenance tests and read-only expansion/contract pre/post/drift audit. No catalogue FK, slug uniqueness or UUID default. |

`apps/web/src/db/schema.ts` drives runtime and Drizzle generation.
`apps/web/drizzle/schema.ts` and `drizzle/relations.ts` are historical introspection
artifacts, not imported by runtime or configured as migration inputs. Their old
saved-job relationships must not be copied into new migrations. The expansion
adds the new reference declaration without recreating historical constraints.
Historical migration SQL/snapshots are immutable evidence, not runtime writers.
The retained `company` table remains owned by the catalogue/history owners above.
Final service bridge-code removal does not retire their inbound or transitive
relationships, grant a table drop, or reactivate a producer.

## Active consumers and producers

| Path / surface | Owner and target | Behavioral contract |
| --- | --- | --- |
| `src/lib/services/company.ts`, `company-detail.ts`, `company-detail-lookup.ts`, `src/lib/search/company-browser-data.ts`, `typesense-posting-detail.ts` | Catalogue reader; keep Typesense | Search/detail publish canonical identity; first-use selectable result can be saved. Public routes may disappear without deleting selections. |
| `src/lib/services/company-references.ts` | Reference materializer owner (#10225/#10228) | Reference-only canonical UUID materialization in the caller's transaction; no legacy catalogue INSERT or mode switch. Promote only verified legacy seeds, preserve existing canonical snapshots, reuse durable references offline. |
| `src/lib/services/watchlists.ts`: create, replacement update, duplicate/copy, individual add, remove/clear | Selection writer (#10225) | Every introduced UUID materializes atomically. Copies reuse durable references. Unauthorized or partial verification changes nothing; remove/clear remain search-independent. |
| `src/lib/services/watchlists.ts`: detail/shared/detail preview, user listing/overview, top-company/activity previews and raw SQL company joins | Durable reader (#10226) | Join `company_reference`; preserve all selected UUIDs and last-known display when search retires/offline. Zero silent omitted references. |
| `src/lib/actions/session-watchlists.ts`: hydration/materialize/import | Session/handoff owner (#10225/#10226) | Resolve preview display from existing references or server-verified catalogue; import through common writer; browser snapshots are not authority. |
| `src/lib/services/watchlist-handoff.ts`, `src/lib/actions/watchlists.ts`, watchlist API handoff routes | Handoff owner (#10225) | Slugs resolve exact UUIDs, all-or-nothing; underlying create uses reference materialization. Unknown slug never creates any-company scope. |
| `src/lib/actions/starred-companies.ts` | Star writer (#10225) | Verified reference on first star, atomic transaction, duplicate race handling, unstar without search. |
| `src/lib/actions/bootstrap.ts`, starred-ID queries | User bootstrap owner | Read membership IDs unchanged; render catalogue queries may omit retired companies but IDs remain durable. |
| `src/lib/services/notification-scheduler.ts`, `src/lib/ai-filter/candidate-loader.ts`, `configuration-service.ts`, `watchlist-matcher.ts` | Notification/AI owner | Read watchlist membership UUIDs unchanged; scope never broadens if a referenced company disappears. Do not activate delivery or alter AI ownership. |
| `src/lib/actions/saved-jobs.ts`, my-jobs/application UI | Saved-jobs owner | Independent snapshots unchanged; no new company FK introduced. |
| `src/lib/actions/request-company.ts` | Request submission owner | Active input/count/GitHub issue metadata unchanged. Resolution columns are compatibility data. |
| `app/api/web/companies/request/[run_id]/status/helpers.ts` | Feature-gated Murmur status owner | Legacy ledger left join stays compatible until independent retirement. No implicit re-enable. |
| Watchlist/company/star UI and session stores | UI owner (#10226/#10227) | Consume last-known bounded display; classify lookup failures; authenticated save/reload browser test exercises actual services. |

## Operational and historical surfaces

| Surface | Classification / disposition |
| --- | --- |
| `apps/crawler/src/sync.py` company/board/mirror helper functions | Library-only compatibility, not a live company producer for web. Do not restore catalogue mirroring as the repair. Crawler UUID allocation/Typesense publication remain authoritative. |
| `apps/web/scripts/company-reference/historical-company-references.ts`, `historical-bridge-pg.test.ts` | Frozen test-only expanded-phase/coexistence/rollback service with commit/blob/SHA256 provenance. Only the historical real-PG suite imports it; active service/browser/staged/public gates remain reference-only. |
| `apps/web/scripts/repair-company-reference.ts` | Explicit one-company legacy operational repair. Deprecated by common reference boundary; historical use is not verification provenance. Expansion trigger labels its writes `legacy_seed`. No unattended production usage. |
| `apps/web/src/db/seed.ts` | Development fixture writer with random legacy IDs. Never production/canonical seed; compatibility trigger supports its fixtures. It cannot supply `typesense` provenance. |
| `apps/web/scripts/backfill-slugs.ts`, `scripts/verify-*retirement*`, `test-job-posting-retirement-pg17.ts`, old mirror cutover tests | Historical migrations/verifiers and isolated fixtures. Keep to prove the prior contract; do not treat mirror coverage as active reference coverage. |
| `apps/web/script/prewarm-company-og-cache.ts`, blog mention snapshots, `src/lib/og/company-og-source-version.ts` | Active static CSV/image generation; independent of web company persistence. No selection/schema change needed. |
| `apps/web/scripts/verify-company-references.ts`, `company-reference-contract.ts` | New read-only aggregate verifier (#10224/#10228). Exact ledger/catalog, compatibility function/trigger, runtime/browser permissions and persisted reference coverage. |
| `.github/workflows/apply-web-routine-migration.yml` | Protected migration executor; reviewed exact SQL hash/timestamp/current main identity and production environment. Runbook binds pre/post evidence; never ad hoc SQL apply. |

Runtime scope audit commands: `rg 'references\(.*company|company\.id' apps/web/src/db/schema.ts`;
search raw SQL and aliases using `rg 'FROM company|JOIN company|from\(company\)|[Jj]oin\(company' apps/web/src apps/web/app`;
search all schema symbols with `rg 'companyDescription|jobBoard|hiringSignal|murmurAcceptLog|resolvedCompanyId|watchlistCompany|followedCompany'`.
New consumers must name their lifecycle owner and extend the shared behavioral
contract. Read-source structural tests are supplementary, never sufficient to
approve retiring the producer behind a user-data foreign key.
