# 2026-08-11 full-surface audit remediation ledger

## Goal

Resolve every actionable issue created, reopened, or materially updated by the
2026-08-11 Jobseek audit. Each issue must move through implementation, focused
tests, repository-wide regression gates where relevant, PR review/CI, safe
rollout when required, production or artifact verification, and only then
closure.

The original remediation baseline is `origin/main` at
`6bf39de7e38c9830176062423130d61cde6ac9fe`. The original audit evidence is on
the isolated `codex/audit-2026-08-11` branch at commit `26d62a252`.

## Safety and closure rules

- Work only in isolated worktrees; never modify the user's existing checkout.
- Do not combine unrelated issues merely to reduce PR count. Combine only when
  one root cause and one rollback boundary satisfy all linked acceptance gates.
- No production mutation, deployment, backup run, restore, restart, queue/data
  mutation, or provider change without an explicit reviewed rollout step.
- Preserve immutable image/digest and least-privilege boundaries.
- Never record secrets, private addresses, tokens, cookies, row contents, or
  raw cloud identifiers in commits, issues, logs, or this ledger.
- `implemented` is not `resolved`. An issue closes only after required CI,
  deployment, restore/production verification, and documentation are complete.
- Reconcile against current `origin/main` before starting every wave; concurrent
  work may already satisfy or change an issue.

## Lifecycle

`queued` -> `active` -> `local-verified` -> `pr-open` -> `merged` ->
`deployed` (when applicable) -> `acceptance-verified` -> `closed`

A repository-only issue may skip `deployed` only when its acceptance criteria
do not require runtime evidence.

## Worktrees and ownership

| Scope | Worktree | Branch | Owner | State |
|---|---|---|---|---|
| Orchestration and ledger | `/Users/Viktor/jobseek-remediation-2026-08-11` | `codex/audit-remediation-2026-08-11` | root | active |
| #6617 monitor outcome metrics | `/Users/Viktor/jobseek-fix-6617` | `codex/fix-6617-monitor-metrics` | root | local-verified; publish blocked by missing `gh` |
| #6619 backup alerts/delivery | `/Users/Viktor/jobseek-fix-6619` | `codex/fix-6619-backup-alerts` | subagent `fix_6619_alerts` | local-verified; publish blocked by missing `gh` |
| #6620 Typesense backup | `/Users/Viktor/jobseek-fix-6620` | `codex/fix-6620-typesense-backup` | subagent `fix_6620_typesense_backup` | local-verified; publish blocked by missing `gh` |
| #6621 PostgreSQL backup image | `/Users/Viktor/jobseek-fix-6621` | `codex/fix-6621-postgres-backup-image` | subagent `fix_6621_postgres_backup` | local-verified; publish blocked by missing `gh` |
| #6618 zero-transition search counts | `/Users/Viktor/jobseek-fix-6618` | `codex/fix-6618-zero-counts` | subagent `fix_6618_zero_counts` | local-verified; superseded by integration branch |
| #6622 scheduled Typesense acknowledgements | `/Users/Viktor/jobseek-fix-6622` | `codex/fix-6622-scheduled-typesense-ack` | subagent `fix_6622_scheduled_ack` | local-verified; superseded by integration branch |
| #6618 + #6622 refresh integration | `/Users/Viktor/jobseek-fix-6618-6622-integration` | `codex/fix-typesense-refresh-integrity` | root | local-verified; publish blocked by missing `gh` |
| #6623 exporter acknowledgement/cursor | `/Users/Viktor/jobseek-fix-6623` | `codex/fix-6623-typesense-ack` | subagent `fix_6623_cdc_ack` | local-verified; publish blocked by missing `gh` |
| #6625 labeller upload integrity | `/Users/Viktor/jobseek-fix-6625` | `codex/fix-6625-labeller-upload-integrity` | subagent `fix_6625_upload_integrity` | local-verified; publish blocked by missing `gh` |
| #6626 labeller scrub integrity | `/Users/Viktor/jobseek-fix-6626` | `codex/fix-6626-labeller-scrub-integrity` | subagent `fix_6626_scrub_integrity` | local-verified; publish blocked by missing `gh` |
| #6624 Typesense payload reconciliation | `/Users/Viktor/jobseek-fix-6624` | `codex/fix-6624-typesense-reconciliation` | subagent `fix_6624_reconciliation` | local-verified; integration with #6631 pending |
| #6627 labelled-sample diversity | `/Users/Viktor/jobseek-fix-6627` | `codex/fix-6627-labeller-sampling` | subagent `fix_6627_sampling_contract` | local-verified; publish blocked by missing `gh` |
| #6628 crawler test hermeticity | `/Users/Viktor/jobseek-fix-6628` | `codex/fix-6628-test-hermeticity` | root | local-verified; publish blocked by missing `gh` |
| #6629 sunset automation docs | `/Users/Viktor/jobseek-fix-6629` | `codex/fix-6629-sunset-automation-docs` | root | local-verified; publish blocked by missing `gh` |
| #6631 PostgreSQL connection budget | `/Users/Viktor/jobseek-fix-6631` | `codex/fix-6631-postgres-pool-budget` | subagent `fix_6631_pool_budget` | second-opinion blockers under correction |
| #6632 Typesense snapshot headroom | `/Users/Viktor/jobseek-fix-6632` | `codex/fix-6632-typesense-headroom` | root | local-verified; stacked on #6620; production acceptance pending |
| #6633 retired units and host logs | `/Users/Viktor/jobseek-fix-6633` | `codex/fix-6633-host-hygiene` | subagent `fix_6633_host_hygiene` | active |
| #3213 public API status semantics | `/Users/Viktor/jobseek-fix-3213` | `codex/fix-3213-api-error-statuses` | subagent `fix_3213_api_statuses` | active |

## Issue register

| Wave | Issue | Surface | Severity | State | Branch/PR | Required terminal evidence |
|---:|---|---|---|---|---|---|
| 0 | [#6619](https://github.com/colophon-group/jobseek/issues/6619) | backup alert evaluation and delivery | high | local-verified | `7dd22d3f5`; PR pending | distinct healthy rule instances, approved delivery-path exercise |
| 0 | [#6620](https://github.com/colophon-group/jobseek/issues/6620) | Typesense backup consistency | high | local-verified | `a0a9f8fac`; PR pending | fresh artifact plus isolated restore under ordinary writes |
| 0 | [#6621](https://github.com/colophon-group/jobseek/issues/6621) | web PostgreSQL backup image lifecycle | high | local-verified | `91e3551dd`; PR pending | GC sequence, two timer cycles, fresh isolated restore |
| 1 | [#5924](https://github.com/colophon-group/jobseek/issues/5924) | fleet lifecycle/protection/reboots/images | medium | queued | — | protections/labels plus safe reboot and post-check evidence |
| 1 | [#6629](https://github.com/colophon-group/jobseek/issues/6629) | sunset Codex automation docs | low | local-verified | `d5b23a1e4`; PR pending | repository search contains no active desktop deployment instructions |
| 1 | [#6631](https://github.com/colophon-group/jobseek/issues/6631) | PostgreSQL connection budget | high | review pending | `41372eea5`; root review pending | normal-load steady state below accepted budget and alert margin |
| 1 | [#6632](https://github.com/colophon-group/jobseek/issues/6632) | Typesense snapshot headroom | medium | local-verified | `1d6984406`; PR pending, stacked on #6620 | measured memory/staging envelope and seven days without emergency image GC |
| 1 | [#6633](https://github.com/colophon-group/jobseek/issues/6633) | retired units and host logs | low | active | `codex/fix-6633-host-hygiene` | no obsolete failed unit/container; bounded log policy verified |
| 2 | [#6617](https://github.com/colophon-group/jobseek/issues/6617) | monitor metrics | medium | local-verified | `60f7fed26`; PR pending | one outcome emission per logical task and live ratio recheck |
| 2 | [#6618](https://github.com/colophon-group/jobseek/issues/6618) | zero-transition search counts | medium | local-verified | `db61e043a` on integration branch; PR pending | every collection transitions positive counts to zero in Typesense |
| 2 | [#6622](https://github.com/colophon-group/jobseek/issues/6622) | scheduled Typesense import acknowledgements | medium | local-verified | `36dc2ec41`, `37f575df0` on integration branch; PR pending | rejected import fails cron and blocks dependent pruning |
| 2 | [#6623](https://github.com/colophon-group/jobseek/issues/6623) | exporter acknowledgement/cursor | medium | local-verified | `5449d49ce`, `27258bc73`; PR pending | malformed/truncated acknowledgements cannot advance CDC cursor |
| 2 | [#6624](https://github.com/colophon-group/jobseek/issues/6624) | Typesense payload reconciliation | medium | local-verified | `c0c9ad79a`; integration with #6631 pending | bounded same-ID/state drift detection and verified repair |
| 2 | [#6625](https://github.com/colophon-group/jobseek/issues/6625) | labeller upload integrity | medium | local-verified | `73412b8e5`; PR pending | malformed local JSON prevents all publication |
| 2 | [#6626](https://github.com/colophon-group/jobseek/issues/6626) | labeller scrub integrity | medium | local-verified | `92fcc181c`, `46038ceef`; PR pending | malformed remote JSONL prevents rewrite |
| 2 | [#6627](https://github.com/colophon-group/jobseek/issues/6627) | labelled-sample diversity | low | local-verified | `d66e0ea52`; PR pending | documented weighted profession/locale behavior under tests |
| 2 | [#6628](https://github.com/colophon-group/jobseek/issues/6628) | crawler test hermeticity | low | local-verified | `a72769a02`; PR pending | full suite has no unawaited warnings or host `gh` dependency |
| 3 | [#2640](https://github.com/colophon-group/jobseek/issues/2640) | Explore initial rendering | medium | queued | — | localized initial HTML has content/H1 and filter hydration works |
| 3 | [#3213](https://github.com/colophon-group/jobseek/issues/3213) | public API error statuses | medium | active | `codex/fix-3213-api-error-statuses` | invalid search parameters return stable HTTP 400 contract |
| 3 | [#6131](https://github.com/colophon-group/jobseek/issues/6131) | build-time external reads | medium | queued | — | deterministic bounded build during Typesense failure |
| 3 | [#6638](https://github.com/colophon-group/jobseek/issues/6638) | watchlist URL handoff | medium | queued | — | delayed bootstrap creates exactly once with every supported filter |
| 3 | [#6639](https://github.com/colophon-group/jobseek/issues/6639) | typeahead race/fan-out | medium | queued | — | stale completions ignored, failures caught, request budget tested |
| 3 | [#6640](https://github.com/colophon-group/jobseek/issues/6640) | homepage image priority | low | queued | — | measured LCP candidate and corrected production fetch order |
| 3 | [#6641](https://github.com/colophon-group/jobseek/issues/6641) | search accessibility | medium | queued | — | localized stable names, controls relationship, AT/keyboard check |
| 3 | [#6642](https://github.com/colophon-group/jobseek/issues/6642) | page landmarks/headings | low | queued | — | exactly one main/H1 on homepage and all auth routes |
| 3 | [#6643](https://github.com/colophon-group/jobseek/issues/6643) | image cache configuration | low | queued | — | unsupported override removed and live behavior documented |
| 3 | [#6644](https://github.com/colophon-group/jobseek/issues/6644) | missing-resource HTTP semantics | low | queued | — | missing/public/private routes satisfy 404 and privacy contracts |

## Checkpoint log

| UTC | Completed | Next | Blocker or residual verification |
|---|---|---|---|
| 2026-08-11 12:56 | Remediation goal created for all 27 audit-linked issues. | Reconcile current main/issues and create isolated worktrees. | None. |
| 2026-08-11 13:00 | Current main `6bf39de7e` fetched; every issue verified open and unassigned; four fresh worktrees created. | Implement #6619/#6620/#6621 in parallel. | Runtime acceptance requires reviewed deploy/restore steps after merge. |
| 2026-08-11 13:12 | #6629 implemented and committed with systemd-only inventory, explicit sunset boundary, and CI docs guard. | Publish focused PR when GitHub CLI prerequisite is available; continue critical wave. | `gh` is not installed; publish workflow requires it. Relevant docs tests pass; broader script suite has four unrelated missing-`jq` failures. |
| 2026-08-11 13:14 | #6620 reviewed and independently reverified: manifest-bound snapshot data/alias contract, bounded alias-cutover retries, restore-side digest/read/count checks; 47 focused tests, Ruff, formatting, Bash syntax, and diff checks pass. Current `origin/main` advanced independently to `01934c904`. | Publish #6620 after rebasing and run a reviewed production backup plus isolated restore; #6631 implementation is active in a separate worktree. | `gh` remains unavailable. #6620 cannot reach acceptance until a fresh artifact is restored while normal writes continue. |
| 2026-08-11 13:33 | #6619 and #6621 passed root review and independent focused reruns (86 and 149 tests respectively); #6617 moved terminal metric ownership to schedulers, added a real processor-to-worker seam, and passed 274 focused tests. #6623/#6626 are active and #6625 awaits root review. Current main is `7201183a0`. | Review the labeller/CDC branches, then start the remaining crawler integrity issues while critical branches await publication. | `gh` remains unavailable. All three critical fixes still require reviewed deployment and live acceptance; #6617 requires the production metric/log ratio to converge near 1. |
| 2026-08-11 13:49 | #6623 now rejects exact-cardinality, malformed, truncated, explicit-failure, and decoder-failure acknowledgements without advancing CDC; #6625/#6626 fail closed before any remote mutation and passed root review. #6618/#6622/#6627/#6628 are active. #6631 is committed and under root review. | Finish #6628 full-suite warning gate and review #6631, then continue through the remaining crawler and infrastructure wave. | Publishing remains blocked by missing `gh`; production-dependent issues remain open until post-merge live acceptance. |
| 2026-08-11 14:03 | #6627 implemented bounded deterministic occupation/locale rarity weighting and passed 182 labeller tests plus Pyright; #6628 removed all unawaited test debt, isolated missing-CLI behavior, enabled warning-as-error policy, and passed the exact CI-shaped suite (7,656 pass, 20 skip, zero warnings). #6624 is active. | Complete root review of #6631 and the active Typesense fixes, then start the next infrastructure/code wave. | `gh` remains unavailable; all local-verified branches await rebase/publication, and runtime acceptance remains outstanding where specified. |
| 2026-08-11 14:10 | #6618 and #6622 passed independent review and were semantically integrated on current main `8f9be7a0a`: retained documents now receive explicit zero/false transitions, all upstream facets validate before the first write, every scheduled import requires exact success, and failed zero updates block later collections/pruning. Combined suite: 129 pass plus Ruff/format/Pyright. #6633 is active; #6631 has an independent second-opinion review in progress. | Finish #6631 review and #6624/#6633 implementations; then start Typesense headroom and fleet lifecycle work. | Real Typesense read-back and disposable rejection exercises remain post-merge acceptance; publishing is still blocked by missing `gh`. |
| 2026-08-11 14:27 | #6624 added bounded full-payload drift detection and a repair path (`c0c9ad79a`, 208 focused tests). #6632 now writes one Typesense snapshot copy directly onto a dedicated fail-closed mount, enforces an exact 3 GiB/2.5 GiB memory contract, and passed 66 backup/deployment tests plus Ruff, formatting, Pyright, Bash syntax, and docs checks (`1d6984406`). #3213 is active. | Obtain independent review of #6632; finish #6631 corrections and #6633/#3213; semantically integrate #6631 before #6624. | #6632 still needs a reviewed volume/container rollout and seven clean days; all publication remains blocked by missing `gh`. |
