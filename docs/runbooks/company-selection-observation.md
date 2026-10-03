# Company selection observation

The `Company selection observation` workflow retains bounded company-selection
mutation counts during the company-reference rollout. It reads production Vercel
request logs across deployments and writes no application or provider data. The
workflow stays disabled while repository variable
`COMPANY_REFERENCE_OBSERVATION_START` is unset. After the bridge deployment and
its authenticated canary succeed, the rollout owner sets that variable to the
confirmed instrumentation start as an ISO8601 UTC timestamp. Changing this start
creates a new observation period; earlier checkpoints cannot certify the new
period. Do not reset the period to hide a missed checkpoint.

The active Vercel scope was independently checked with CLI `whoami --scope ...
--format json` on 2026-10-03 and reported **Hobby**. Identity-bearing output was
captured privately; only the plan escaped. Vercel documents runtime-log retention
as Hobby **1 hour**, Pro **24 hours**, Enterprise **72 hours**, or **30 days** with
Observability Plus. Plus queries cover at most 14 consecutive days. The collector
uses the checked baseline plan and does not assume or purchase Plus.
[Vercel runtime-log limits and retention](https://vercel.com/docs/logs/runtime).

## Execution and evidence

The workflow runs every 15 minutes at minutes 3, 18, 33, and 48, and permits a
manual run only by `viktor-shcherb`, with that same triggering actor, on `main`.
It uses the existing `Production` environment and its branch policy, the existing
Vercel token/project/organization secrets, and a GitHub token with `actions: read`
and `contents: read`. Collection uses Vercel CLI **62.1.0** pinned through `pnpm
dlx`; the existing deployment CLI pin is unchanged. Raw CLI stdout/stderr,
identity, request IDs, paths, domains, messages, and errors never become workflow
logs or artifacts. Only aggregate JSON, fixed completeness reasons, time windows,
and source/run identities escape the collector. Failure messages are constants.

Each run queries fixed, closed UTC 15-minute windows at least five minutes old.
It replays retained recent windows to include delayed ingestion and fills missing
windows still within retention. It applies a two-minute retention safety margin
at each query boundary. Requests are assigned to half-open windows using the
CLI's **request timestamp**; child log-event timestamps are unavailable. Thus
individual event-time precision and final ingestion are not certified.

Each immutable `company-selection-checkpoints-<run>-<attempt>` artifact contains
exactly `checkpoint.json`, with up to seven days of aggregate checkpoints and a
SHA256 of the payload. Artifact retention is 14 days. A later run retrieves the
newest valid completed main-workflow snapshot among the most recent 30 runs,
verifies the originating workflow/event/actor, source revision, run attempt,
artifact ZIP digest, fixed schema, bounded operation/outcome/reason values, time
bounds, and payload digest. The ZIP is capped at4MiB compressed and16MiB inflated (enough for the full
bounded seven-day outcome matrix) and parsed in bounded memory; no archive paths
are extracted. A failed run with a valid partial checkpoint still contributes
honest evidence. This cumulative snapshot avoids downloading 672 separate
artifacts for a seven-day report. A skipped run has no artifact; the next run
fills its missing window only while provider retention permits it.

Workflow stdout and the job summary contain hourly, 24-hour, seven-day, and
since-rollout reports. During warm-up, longer reports declare
`duration_not_reached`. A gap or partial window fails the collector step but its
bounded artifact is still uploaded. The next run can repair a recoverable gap;
expired gaps remain visible. Replayed counts replace earlier counts rather than
adding them. If counts decrease, the checkpoint retains the observed maxima,
marks `replay_count_regression`, and cannot certify zero failures.

At each hourly checkpoint, check `reports.sinceRollout.completeness` and all
bounded failure outcomes. At 24 hours and seven days, require the corresponding
report's duration, window coverage, positive telemetry observation, and absence
of `rolloutAcceptanceBlockers`. Inspect counts for `database_foreign_key`,
`identity_conflict`, `lookup_unavailable`, `lookup_miss`, and other failures; zero
counts alone do not establish success. Keep the independent authenticated canary
and database drift evidence alongside these aggregate observations.

## Completeness limits and response

Vercel CLI 62.1.0 was inspected locally at `dist/commands-bulk.js`:
`fetchRequestLogs` maps each request row to a top-level selected display message
and a `logs[]` array; `fetchAllRequestLogs` pages using `page`/`hasMoreRows` until
`--limit` requests, then stops. JSON output is one request per line and has no
cursor, exhausted marker, or truncation summary. The collector counts exact
`{event, operation, outcome}` JSON from every child message and never counts the
top-level display message twice. It subdivides a limit-filled window, discarding
the parent counts, until unsaturated or until the one-second/budget limit marks
it partial. Queries are bounded to 1,000 requests per call, 24 calls/two minutes
per run, 64 MiB transport per call, and 8 MiB pending record memory.
[Vercel CLI log options](https://vercel.com/docs/cli/logs).

Installed 62.1.0 does not implicitly restrict historical queries to the current
git branch; its `--no-branch` is a deprecated no-op. Current CLI documentation
has different branch wording. The collector passes explicit project, scope,
production environment, and absolute bounds without a branch, deployment, or
follow flag. Version upgrades require rechecking the installed implementation
and the parsing/coverage tests, not simply changing the pin.

Vercel can retain only the latest logs after exceeding **256 lines or 1 MiB per
request**, with a **256 KiB per-line** limit. The collector flags visible
truncation/cap risks, malformed or unrecognized telemetry, duplicate request
rows, failed queries, saturation, and missed/expired windows. The CLI does not
prove absence of provider-side dropped messages, so even an exhausted report
states `providerCaptureGuarantee: unknown`. Its zero claim means only
`observed_only_under_conditional_provider_capture`, never a universal claim of
zero mutations or failures. No positive telemetry anywhere in the period also
blocks zero claims: a missing instrumented deployment must not look healthy.
[Vercel runtime-log limits](https://vercel.com/docs/logs/runtime).

GitHub schedules can be delayed or dropped, particularly during load. Hobby's
one-hour retention makes any outage approaching an hour a rollout evidence
risk. Run the owner manual workflow promptly when a run fails or is delayed.
Once a window expires without a checkpoint, stop acceptance for that observation
period and record the gap; do not synthesize zero counts or query seven days of
expired Vercel logs. Restarting a full acceptance observation requires an explicit
owner decision and a recorded new period. No logging drain, paid subscription,
provider configuration, or notification activation is part of this workflow.

## Local review

Run `node --test scripts/collect-company-selection-telemetry.test.mjs` and
`actionlint .github/workflows/company-selection-observation.yml`. The unset-start
CLI path is a tested no-op. A trusted local operator can use `--local-cli` with
normal existing CLI authentication and explicit project/scope environment values
for a read-only rehearsal. It uses `/opt/homebrew/bin/vercel`, verifies 62.1.0,
emits only the aggregate report, and loads/writes no GitHub artifact. No secret
belongs in an argument, repository file, report, or artifact.
