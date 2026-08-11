# 2026-08-11 full-surface audit remediation ledger

## Goal

Resolve every actionable issue created, reopened, or materially updated by the
2026-08-11 Jobseek audit. Each issue must move through implementation, focused
tests, repository-wide regression gates where relevant, PR review/CI, safe
rollout when required, production or artifact verification, and only then
closure.

The remediation baseline is `origin/main` at
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
| #6619 backup alerts/delivery | `/Users/Viktor/jobseek-fix-6619` | `codex/fix-6619-backup-alerts` | subagent `fix_6619_alerts` | active |
| #6620 Typesense backup | `/Users/Viktor/jobseek-fix-6620` | `codex/fix-6620-typesense-backup` | subagent `fix_6620_typesense_backup` | local-verified; publish blocked by missing `gh` |
| #6621 PostgreSQL backup image | `/Users/Viktor/jobseek-fix-6621` | `codex/fix-6621-postgres-backup-image` | subagent `fix_6621_postgres_backup` | active |
| #6629 sunset automation docs | `/Users/Viktor/jobseek-fix-6629` | `codex/fix-6629-sunset-automation-docs` | root | local-verified; publish blocked by missing `gh` |
| #6631 PostgreSQL connection budget | `/Users/Viktor/jobseek-fix-6631` | `codex/fix-6631-postgres-pool-budget` | subagent `fix_6631_pool_budget` | active |

## Issue register

| Wave | Issue | Surface | Severity | State | Branch/PR | Required terminal evidence |
|---:|---|---|---|---|---|---|
| 0 | [#6619](https://github.com/colophon-group/jobseek/issues/6619) | backup alert evaluation and delivery | high | active | `codex/fix-6619-backup-alerts` | distinct healthy rule instances, approved delivery-path exercise |
| 0 | [#6620](https://github.com/colophon-group/jobseek/issues/6620) | Typesense backup consistency | high | local-verified | `a0a9f8fac`; PR pending | fresh artifact plus isolated restore under ordinary writes |
| 0 | [#6621](https://github.com/colophon-group/jobseek/issues/6621) | web PostgreSQL backup image lifecycle | high | active | `codex/fix-6621-postgres-backup-image` | GC sequence, two timer cycles, fresh isolated restore |
| 1 | [#5924](https://github.com/colophon-group/jobseek/issues/5924) | fleet lifecycle/protection/reboots/images | medium | queued | — | protections/labels plus safe reboot and post-check evidence |
| 1 | [#6629](https://github.com/colophon-group/jobseek/issues/6629) | sunset Codex automation docs | low | local-verified | `d5b23a1e4`; PR pending | repository search contains no active desktop deployment instructions |
| 1 | [#6631](https://github.com/colophon-group/jobseek/issues/6631) | PostgreSQL connection budget | high | active | `codex/fix-6631-postgres-pool-budget` | normal-load steady state below accepted budget and alert margin |
| 1 | [#6632](https://github.com/colophon-group/jobseek/issues/6632) | Typesense snapshot headroom | medium | queued | — | measured memory/staging envelope and seven days without emergency image GC |
| 1 | [#6633](https://github.com/colophon-group/jobseek/issues/6633) | retired units and host logs | low | queued | — | no obsolete failed unit/container; bounded log policy verified |
| 2 | [#6617](https://github.com/colophon-group/jobseek/issues/6617) | monitor metrics | medium | queued | — | one outcome emission per logical task and live ratio recheck |
| 2 | [#6618](https://github.com/colophon-group/jobseek/issues/6618) | zero-transition search counts | medium | queued | — | every collection transitions positive counts to zero in Typesense |
| 2 | [#6622](https://github.com/colophon-group/jobseek/issues/6622) | scheduled Typesense import acknowledgements | medium | queued | — | rejected import fails cron and blocks dependent pruning |
| 2 | [#6623](https://github.com/colophon-group/jobseek/issues/6623) | exporter acknowledgement/cursor | medium | queued | — | malformed/truncated acknowledgements cannot advance CDC cursor |
| 2 | [#6624](https://github.com/colophon-group/jobseek/issues/6624) | Typesense payload reconciliation | medium | queued | — | bounded same-ID/state drift detection and verified repair |
| 2 | [#6625](https://github.com/colophon-group/jobseek/issues/6625) | labeller upload integrity | medium | queued | — | malformed local JSON prevents all publication |
| 2 | [#6626](https://github.com/colophon-group/jobseek/issues/6626) | labeller scrub integrity | medium | queued | — | malformed remote JSONL prevents rewrite |
| 2 | [#6627](https://github.com/colophon-group/jobseek/issues/6627) | labelled-sample diversity | low | queued | — | documented weighted profession/locale behavior under tests |
| 2 | [#6628](https://github.com/colophon-group/jobseek/issues/6628) | crawler test hermeticity | low | queued | — | full suite has no unawaited warnings or host `gh` dependency |
| 3 | [#2640](https://github.com/colophon-group/jobseek/issues/2640) | Explore initial rendering | medium | queued | — | localized initial HTML has content/H1 and filter hydration works |
| 3 | [#3213](https://github.com/colophon-group/jobseek/issues/3213) | public API error statuses | medium | queued | — | invalid search parameters return stable HTTP 400 contract |
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
