# Bounded runtime metrics v1

These are target boundary definitions for the v1 adapters; they do not claim
that today's in-process Python extraction counters already implement the
framed terminal lifecycle. Python and Go adapters must converge on these names
before comparison. Arbitrary
board IDs, request IDs, operation IDs, hosts, URLs, artifact handles, error
messages, and config revisions belong in sampled structured logs/traces, never
Prometheus labels.

| Metric | Type | Labels (closed values) | Definition |
|---|---|---|---|
| `crawler_runtime_executions_total` | counter | `stage={monitor,scrape,browser}`, `implementation={python,go}`, `outcome={success,error,cancelled,incomplete,unsupported}` | Executor/protocol outcome only. `success` means one valid `eligible_for_commit` terminal; it does not assert persistence. Disconnect without terminal is `incomplete`. |
| `crawler_runtime_commits_total` | counter | `stage`, `implementation`, `outcome={committed,stale_fence,rejected,error}` | Caller/persistence outcome. Increment only after live #7938 token+epoch revalidation immediately before a mutation; `committed` means the mutation succeeded. |
| `crawler_runtime_execution_duration_seconds` | histogram | `stage`, `implementation` | Active executor time from accepted start/resume through terminal; excludes time blocked on frame credit. Buckets: `.1,.25,.5,1,2.5,5,10,30,60,120,300,900`. |
| `crawler_runtime_output_items_total` | counter | `stage`, `implementation` | Monitor URL items or successful scrape/browser results in validated frames, whether later commit-eligible or not. |
| `crawler_runtime_protocol_total` | counter | `implementation`, `event={disconnect,resume,deduplicated,ambiguous,limit,sequence,cancel,stale}`, `outcome={accepted,rejected}` | One protocol transition; no dynamic error label. |
| `crawler_runtime_frame_bytes` | histogram | `implementation`, `direction={in,out}`, `frame={control,origin,monitor,scrape,browser,artifact,error,terminal}` | Complete length-prefix plus payload bytes. Buckets: `256,1024,4096,16384,65536,262144,1048576`. |
| `crawler_runtime_backpressure_seconds` | histogram | `implementation`, `stage` | Producer time blocked with zero frame credit. Buckets: `.001,.01,.05,.1,.5,1,5,30,120`. |
| `crawler_browser_backend_lifecycle_total` | counter | `backend={chromium,lightpanda}`, `event={start,session,target,stop}`, `outcome={success,target_lost,session_lost,timeout,error}` | One bounded backend lifecycle transition. |

The theoretical largest series set for this table is 176 before histogram
bucket expansion: 30 execution counters, 24 commit counters, 6 duration
histograms, 6 output counters, 32 protocol counters, 32 frame histograms, 6
backpressure histograms, and 40 browser counters (some combinations are
intentionally never emitted).
Build/release/cohort/region/provider-family dimensions remain in the
epic scorecard and #7940; they are not added ad hoc to these boundary metrics.

Service-level comparison keeps executor eligibility separate from persistence.
Executor `success` is counted after a valid commit-eligible terminal. A partial
frame followed by error, cancellation, disconnect, unsupported capability, or
missing terminal never increments it. Only `crawler_runtime_commits_total`
records the later live-fenced mutation outcome.
