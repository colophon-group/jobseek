# Jobseek Documentation

This directory is the project documentation index. The numeric prefixes are
historical and have gaps, so use this page rather than filename order when
looking for the current reference.

Status tags:

- `[reference]` - maintained architecture, data, or implementation reference.
- `[routine]` - repeatable operating process.
- `[runbook]` - incident, migration, or one-off operational guide.
- `[historical]` - preserved background that may not describe the current
  implementation.
- `[adr]` - architectural decision record.

## Start Here

- [00 - System Overview](00-overview.md) `[reference]` - project map, main
  pipelines, and high-level design decisions.
- [agents.md](agents.md) `[reference]` - reasoning guidance for developer
  agents working in this repository.
- [07 - System Design](07-system-design.md) `[reference]` - infrastructure,
  data paths, runtime components, and web/crawler subsystem details.
- [02 - Data Schema](02-data-schema.md) `[reference]` - company and board CSV
  schemas, sync behavior, and validation commands.

## Crawler And Data

- [03 - Crawler Architecture](03-crawler-architecture.md) `[reference]` -
  Redis queues, crawler workers, local Postgres, exporter CDC, and failure
  handling.
- [04 - Monitors and Scrapers](04-monitors-and-scrapers.md) `[reference]` -
  monitor/scraper types, configuration examples, and extraction rules.
- [08 - Job Data Fields](08-job-data-fields.md) `[reference]` - normalized job
  fields and source mappings.
- [09 - Enrichment](09-enrichment.md) `[reference]` - enrichment pipeline
  shape, provider boundaries, and rollout constraints.
- [10 - Parallel Agent Pipeline](10-parallel-agent-pipeline.md) `[historical]`
  - earlier multi-agent company onboarding design and backlog notes.
- [21 - ATS Inventory Candidate Runner](21-ats-inventory-runner.md) `[reference]`
  - data-only upstream boundary, compatibility coverage, validated cache, and
  unsupported-family quarantine contract.
- [23 - Go and Lightpanda Crawler Migration](23-go-lightpanda-migration.md)
  `[reference]` - global-scale runtime replacement boundaries, capacity floor,
  cutover safety, replay, metrics, Murmur coordination, and retirement plan.
- [24 - Global Crawler Capacity Envelope](24-global-crawler-capacity-envelope.md)
  `[reference]` - versioned global-v1 workload, sharded topology, simultaneous
  failure matrix, storage and cost budgets, synthetic routing evidence, and
  production-equivalent execution gate.

## Search, SEO, And Web Read Paths

- [11 - Typesense](11-typesense.md) `[reference]` - Typesense deployment,
  schemas, aliases, indexing, web read paths, and reconciliation.
- [12 - Typesense Benchmarks](12-typesense-benchmarks.md) `[reference]` -
  measured Typesense query performance.
- [Typesense Footprint Investigation (2026-08-26)](typesense-footprint-investigation-2026-08-26.md)
  `[investigation]` - issue #8033 production baseline, complete field-consumer
  matrix, isolated schema lab, and evidence-backed optimization trajectories.
- [13 - SEO and IndexNow](13-seo-and-indexnow.md) `[reference]` - company page
  indexing policy, active IndexNow paths, and current observability limits.

## Agent And Automation Workflows

- [01 - Agent Workflow](01-agent-workflow.md) `[reference]` - company-request
  resolver workflow and `ws` usage.
- [05 - Auto-Merge](05-auto-merge.md) `[reference]` - low-risk config PR merge
  policy.
- [16 - Murmur Codex MCP Transition](16-murmur-codex-mcp-transition.md)
  `[runbook]` - Codex-accessible Murmur MCP transition plan and guardrails.
- [17 - Codex Migration Verification Runbook](17-codex-migration-verification-runbook.md)
  `[runbook]` - pilot checks and rollback criteria for Codex migration
  surfaces.
- [18 - Hetzner Codex Runner Deployment](18-codex-automation-deployment.md)
  `[runbook]` - production systemd runner inventory, deployment procedure,
  maintenance checks, recovery boundary, and harness-invariant
  contracts.

## Operations And Routines

- [14 - Daily Error Review Routine](14-error-review-routine.md) `[routine]` -
  crawler error review workflow.
- [15 - Daily Labelled-Postings Routine](15-data-sampling-routine.md)
  `[routine]` - gold-dataset sampling, labelling, validation, and upload.
- [16 - Hetzner Maintenance](16-hetzner-maintenance.md) `[runbook]` -
  disk/headroom checks and Docker cleanup on Hetzner hosts.
- [18 - Vercel Fluid CPU Regression Gate](18-vercel-fluid-cpu.md) `[runbook]` -
  clean-window measurement protocol, route budgets, functionality checks, and
  rollback criteria for web CPU interventions.
- [19 - Hetzner Data Backup and Recovery](19-data-backup-recovery.md)
  `[runbook]` - encrypted PostgreSQL and Typesense backups, restore drills,
  retention, scheduling, and replacement gates for legacy server backups.
- [20 - Redis Capacity, Cleanup, and Recovery](20-redis-capacity.md)
  `[runbook]` - key-family budgets, bounded orphan cleanup, noeviction pressure
  behavior, and RDB scheduler rebuild procedures.
- [20 - Supabase Free Downgrade](20-supabase-free-downgrade.md) `[runbook]` -
  guarded migration repair, saved-job cutover order, and production evidence
  gates for removing the crawler-owned mirror.
- [22 - PostgreSQL Connection Budget](22-postgresql-connections.md) `[runbook]` -
  exact service and deploy-overlap pool ceilings, ownership metrics, reserves,
  idle-transaction controls, and seven-day production acceptance.
- [Didi Reactivation Runbook](runbook-didi-reactivate-2026-05-10.md)
  `[runbook]` - historical Didi reactivation notes.

## Historical Plans

- [Multi-Language Job Postings](multi-language-job-postings.md) `[historical]`
  - the original multilingual posting migration checklist. The implemented
  decision is captured in
  [ADR-001](adr/001-multi-language-job-postings.md).

## Architectural Decisions

- [ADR-001 - Multi-Language Job Postings](adr/001-multi-language-job-postings.md)
  `[adr]` - implemented language metadata and detection model.
- [ADR-002 - Local Postgres for Crawler Runtime Source of Truth](adr/002-local-postgres-runtime-sot.md)
  `[adr]` - local Postgres is authoritative for crawler-owned runtime data.
- [ADR-003 - CSV Configuration Source of Truth](adr/003-csv-configuration-source-of-truth.md)
  `[adr]` - company and board configuration remain CSV-backed until a future
  migration explicitly supersedes the contract.
- [ADR-004 - Better Auth for Web Authentication](adr/004-better-auth-web-authentication.md)
  `[adr]` - Better Auth remains the web authentication boundary.
- [ADR-005 - Lingui Babel Macro Pipeline](adr/005-lingui-babel-macro-pipeline.md)
  `[adr]` - Lingui macros are transformed through Babel rather than an
  SWC-only build path.
- [ADR-006 - Crawler Deploy Quiescence and Rollback](adr/006-crawler-deploy-quiescence-and-rollback.md)
  `[adr]` - crawler deploys intentionally quiesce processors and rely on
  rollback/readiness gates.
- [ADR-007 - IndexNow Observability Boundary](adr/007-indexnow-observability-boundary.md)
  `[adr]` - active IndexNow observability uses structured logs until the web
  metrics surface exists.
