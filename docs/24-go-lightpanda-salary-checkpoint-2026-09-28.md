# Resumable migration checkpoint — 2026-09-28

## Scope and stopping point

The user changed this session's goal to **a durable checkpoint another session
can resume**. This checkpoint saves a complete, locally verified **Go salary
candidate**. It does not deploy that candidate or complete the broader
Go + Typesense + self-hosted Lightpanda transition. Python/Chromium still own
production work. Continue implementation from here when the migration resumes.

- Branch: `fix-crawler/go-salary-extraction`, based on latest main
  **`ff249fa0b6b24273c2d8a2c89520ee2e9055d5e9`**.
- Implementation commit:
  **`92dddb8ed65ef505626e98ddf7ede0e4a50e138d`**.
- Draft [PR #10177](https://github.com/colophon-group/jobseek/pull/10177) preserves
  the candidate and this handoff. Required CI/image gates must be read from
  the current PR head; no CI completion or deployment is assumed here.
  **Crawler Deploy Gate intentionally fails while the PR is draft** with
  `Draft PR; no merge authority is granted`. This is the saved stopping state,
  not a salary failure. Reconciliation evaluates it again after the PR is
  marked ready at the supported resumption step.
- Candidate crawler version: **0.13.899**; **not deployed**.
- This document/evidence are a second commit on the same branch. Resolve the
  full draft PR head and required checks from GitHub before continuing:

  ```bash
  gh pr list --repo colophon-group/jobseek --state all \
    --head fix-crawler/go-salary-extraction \
    --json number,url,state,isDraft,headRefOid,baseRefName
  ```

- Isolated managed checkout:
  `/Users/Viktor/.codex/worktrees/go-typesense-transition/jobseek`.
  The primary `/Users/Viktor/jobseek` is dirty and must remain untouched.
  Preserve this branch/candidate before preparing another branch from latest main.

## Candidate implementation and verification

`apps/crawler/go/job-enrichment/salary.go` owns all 18 supported currencies,
ordered extraction families, public range/unified/base-salary parsing and the
CPU five-field result. The three public Python APIs and CPU result dispatch to
this resident Go process when the existing `JOB_ENRICHMENT_ENGINE=go` is
selected. Python is retained as an explicit engine reversal/offline oracle;
there is no automatic fallback after an error.

Preserved behavior includes structured/bare range priority, currency prefix and
suffix resolution, BRL and European separators, multilingual periods, Unicode
classes/case behavior, character-based context windows, mojibake repairs,
gross/net and perk rules, magnitude thresholds, hourly cents, deduplication and
currency/period group ties. Existing irregularities such as reversed CHF ranges
and whole-unit dollar ranges with hourly labels are intentionally preserved.
EUR conversion uses caller-supplied rates, 2080 hours/year, 12 months/year,
float64 operations and Python-compatible ties-to-even rounding. Monthly
annualization keeps its integer intermediate exact before float conversion.

The pinned pure-Go regexp2 **v2.8.0** supports the existing lookarounds. Each
match has a 500 ms timeout, a bounded backtracking stack and cached rune buffer;
the scan deadline is five seconds below the existing ten-second IPC deadline.
Numbers outside the signed 64-bit transport bound fail explicitly. Persistence
still enforces its existing database integer constraints. Limits fail the task
and reap the child; they never truncate output or return empty success.

Verified locally:

- **2,664 frozen compatibility cases**, **902 positive**, all **18 currencies**.
  Includes original regression inputs and period/context/magnitude/Unicode/
  mojibake/aggregation/EUR cases.
- **202 focused Python tests passed**: salary families, CPU annualization,
  reprocessing, bridge lifecycle, and all four public Go salary paths with
  Python oracle functions replaced by rejecting stubs.
- Uncached **Go race tests**, `go vet`, module tidy, formatting, Ruff and
  Pyright passed. An initial Python fixture hit pytest's 30-second deadline
  while running the full Go race suite. CI now runs that suite separately;
  the bridge fixture builds the real binary. The corrected run passed.
- Local installed-protocol verifier passes the complete salary oracle plus
  existing taxonomy, technology, experience, HTML and language fixtures.
  The required CI image step verifies the actual shipped binary offline;
  resolve its current PR result instead of assuming the local check proves it.
  The first Workflow Security run found a missing docs README index entry;
  that entry is now present and the focused docs-index check passed.
- **512 actual stored production descriptions / 265 boards / 14 locales**
  matched the Python salary output exactly, including all ranges, unification,
  public parsed fields and EUR results for the supplied input rates (these
  stored inputs have no rate map; rate conversion is covered by frozen cases).

See [sanitized evidence](evidence/go-salary-checkpoint-2026-09-28.json).
Protected raw inputs/logs are local, mode 0600, under
`/Users/Viktor/.codex/migration-evidence/go-salary/2026-09-28/`; directory mode 0700.
The existing protected stored sample is
`/Users/Viktor/.codex/migration-evidence/go-html-normalization/2026-09-28/stored-sample.json`.
Its SHA-256 is `d88c0a7cb259073e872923e26998282ebae8b91c141cbf6edc26cc58ffa57681`.
Never commit raw posting content. Another host can read canonical stored bytes
through the documented read-only DB workflow; do not fetch publishers again.

The compare run includes both engines and is **parity evidence only**. It is
not a counterbalanced resource experiment. There is no new production salary
success/DB readback or whole-lane CPU/RAM/density/cost evidence at this checkpoint.

## Production authority to preserve

Revalidated **2026-09-28 19:37:36 UTC**, with no production mutations in this
salary session:

- Crawler **v0.13.898**, promoted revision
  **`20b4031ccc7e3056c6ca10b55b68bd5562c0c720`**.
- Deployment **36467631537** succeeded, including promotion at 19:00:41 UTC.
- Crawler image:
  `ghcr.io/colophon-group/jobseek-crawler@sha256:5a1003ecf68d70b58c06a4c4c692c4aad28369754d8807a09a9b96c6d6b0102f`.
- Browser image:
  `ghcr.io/colophon-group/jobseek-crawler-browser@sha256:52763522dfce80079619d18aa0cb7ff372b883ea86b8a9797e19a00255cd266b`.
- **cdom ACTIVE / epoch 133**. Workers, browser, drain, producer, executor,
  claimant and Redis healthy; exporter and Alloy up. Last conservation proof:
  22 ready/zero inflight, 22 selected schedules, 17 already unqueued; zero
  dropped terminals. C2 Kandou remains dark. No fresh cdom cycle is claimed.
- **25 exact selectors** staged at the full promoted revision. Helper:
  `scripts/migration-jsonld-selectors.py`, SHA-256
  `8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`.
  Exact Kandou URL: `https://kandou.bamboohr.com/careers/310`.
- Shared Go language detection was deployed and naturally proven in
  [PR #10174](https://github.com/colophon-group/jobseek/pull/10174). Its durable
  record merged in [PR #10175](https://github.com/colophon-group/jobseek/pull/10175).
  See the current production section of the [resumption plan](24-go-lightpanda-resumption-plan.md).

## Latest Lightpanda

The latest official **September 28 nightly** was rechecked during this session.
The installed ARM64 binary on `murmur` exactly matches its asset checksum.
The mutable tag's old creation date does not identify the binary's age.

- ARM64 asset **594332898**, updated **02:56:09 UTC**, SHA-256
  `e5e3b57fb1c99325c1b66e5f1e25d02199f21d116126a74296578bcc0ae9cd8f`.
- AMD64 asset **594332787**, updated **02:56:04 UTC**, SHA-256
  `e7dfca7686ad4ca5831b5cd741684b2dde6579c9f6046a848206cf5ad39efd3f`.
- Renderer source `fb2b1c865e90ccf67379aa1f303e2b5f5e8f95c0`, image
  `ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:72b73991cc4a6820263970ccf07367b5d9193eb37cb9d742701fb78ea0104a21`.
- Pin manifest: `pilots/go-lightpanda/lightpanda-release.json`.

At resumption compare the official asset IDs/digests to the manifest and
installed `/usr/local/bin/lightpanda`. If upstream changed, update through the
supported cold renderer/deployment workflow. Do not replace a running binary
or use a mutable Docker image tag.

## Exact next steps at resumption

1. Resolve the draft PR/current head above. Fetch latest main in an isolated
   checkout, account for any newer implementation/deployment, and update the
   candidate's base/VERSION if needed. Required CI and Crawler Deploy Gate must
   pass before merge; the draft deployment gate becomes eligible in step 3.
   Fix a concrete failure once observed; do not start another optional
   verification loop after focused checks and required gates suffice.
2. Re-read production `success.env`, B0 receipt and selector state. If revision
   or owner changed, use that authoritative state instead of this historical
   snapshot. Before any crawler deploy/selector mutation, supported rollback
   the active **cdom** owner and clear all **25** selectors under the mutation
   lock at the **full current promoted revision**. For the recorded revision:

   ```bash
   ssh -o BatchMode=yes hetzner-crawler \
     '/home/deploy/scripts/lightpanda-b0-cutover.sh rollback cdom'
   ssh -o BatchMode=yes hetzner-crawler \
     'flock -w 180 /run/lock/jobseek-crawler-mutation.lock python3 - clear 20b4031ccc7e3056c6ca10b55b68bd5562c0c720 https://kandou.bamboohr.com/careers/310' \
     < scripts/migration-jsonld-selectors.py
   ```

   Preserve the complete rollback receipt: restored schedules, terminal drops,
   cleared fences and healthy writer set. Never manually edit `.env`.
3. Mark the candidate ready when continuing. Immediately before the authorized
   merge, refresh PR state/draft/head/base/labels/required checks/mergeability
   and bind merge to that exact head. Never push directly to main or remove a
   deployment hold. Zero approving reviews are required by repository policy.
4. Wait for the **entire** crawler deployment including promotion to succeed.
   Stage the desired 25 selectors using the helper at the **new full promoted
   revision**, then supported `activate cdom`. If ordinary Go Typesense
   reconciliation holds the mutation lock, let its bounded one-shot finish.
   A lock timeout with no mutation is not permission to bypass or interrupt it.
5. Observe **natural** Go salary executions and read canonical DB results,
   failures and content hashes. Compare the same stored bytes and caller rates.
   No forced due times, priority changes, passive-capture duplication or origin
   re-fetches. Save deployed evidence and a new accurate repository checkpoint.
6. Continue location extraction, remaining enabled monitor/detail profiles,
   native workers and persistence, other Python runtime owners and removal of
   Python/Playwright/Chromium. Typesense export/backfill/reconciliation/schema/
   count refresh, configuration sync and Go drain are already migrated.
   Same actual-workload whole-lane CPU/RAM/density/cost plus final supported
   cutover/reversal are still required. Issue #7966 is owner-closed; use its
   criteria and do not reopen it to manage this checkpoint.
