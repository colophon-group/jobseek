# Browser service lanes v1 conformance contract

Status: **inactive offline conformance candidate** for #8248. Nothing in this
directory is imported by the crawler or authorizes a queue, browser-service,
autoscaler, deployment, or routing change.

`model.py` is the Python reference evaluator. A later independent Go
implementation consumes the exact generated corpus and must produce the same
canonical bytes and SHA-256 digests. The sole work reference in output is the
synthetic corpus ordinal; no input or output contains a board, origin, URL,
host, credential, token, browser image, or live timestamp.

## Input and result

The canonical corpus is `{ "format", "cases" }`, with ordered cases shaped as
`{ "id", "input", "expected", "result_digest" }`. An input evaluates both
`lightpanda` and `chromium` on the one supplied integer logical timeline.
Inputs are strict UTF-8 JSON and are canonicalized with sorted object keys,
compact separators, and a final LF for the checked-in corpus. `scenarios.sha256`
is the lowercase SHA-256 of those exact corpus bytes. A case digest hashes the
same canonical serialization of its expected result without a trailing LF.

Each normalized lane result is exactly:

```json
{"decision":"claim","desired_concurrency":1,"lane":"lightpanda","reasons":[],"selected_item_index":0}
```

`decision` is `claim`, `defer`, or `freeze`; a claim has no reasons and a
non-null ordinal, while defer/freeze have a null ordinal and sorted, unique
closed reasons. A malformed document returns only `{ "error": "invalid_input" }`.
It never reflects an input value, parse error, or path.

The lane snapshot includes `queue_shard_id`, `routing_epoch`, and
`engine_owner` in addition to the current revision IDs. These are the
lane-local queue snapshot bindings required by the final zero-proof resolution;
the proof therefore has every required revision/fence comparison even for an
empty lane.

## Semantics

- Lanes are independent: no fallback, capacity borrowing, health masking, or
  sibling-derived decision is permitted.
- Due, current-fence, immutable-assignment, policy-`permit` items are eligible.
  `defer` and `deny` remain conserved but do not enter eligible counts;
  `violation` freezes only its assigned lane.
- Fresh safety telemetry requires utilization `<= 0.85`, headroom `>= 0.15`,
  error-budget burn `<= 1.0`, and no saturation. Unsafe capacity defers without
  scale-out; stale telemetry, exhausted error budget, and saturation freeze.
- With safe eligible backlog, an admitted idle slot claims immediately. Without
  one, at most one lane-local bounded scale-up step is requested after cooldown
  and outside a drain. Desired never drops below inflight and never cancels it.
- Before the 900-second age override, arbitration ranks age plus credits
  `first_time=300`, `monitor=60`, `detail=0`; ties use older eligibility time,
  priority, then ordinal. At 900 seconds, oldest eligible work wins. Thus, if
  `K` currently eligible records are older when an item reaches the override,
  it is selected within `K+1` claim-producing evaluations.
- Scale-to-zero is proof-gated. The exact proof must be fresh (30 seconds),
  complete (900 seconds), lane-bound, zero-census, and not invalidated by an
  entry into eligibility at or after proof completion. It is a target only and
  does not retire Chromium.

## Verification

Run from `apps/crawler/`:

```bash
uv run python contracts/browser/lanes/v1/tools/generate_corpus.py --check
uv run pytest -q contracts/browser/lanes/v1/conformance/python
uv run ruff check contracts/browser/lanes/v1
uv run ruff format --check contracts/browser/lanes/v1
uv run pyright contracts/browser/lanes/v1
```
