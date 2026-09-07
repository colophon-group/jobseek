# Browser service lanes v1 conformance contract

Status: **inactive offline conformance contract** for #8248. Nothing in this
directory is imported by the crawler or authorizes queue, browser-service,
autoscaler, deployment, routing, fallback, replica, or production changes.

The Python reference evaluates one complete global snapshot. The independent Go
evaluator consumes the checked-in Python-generated corpus and must produce the
same canonical result bytes and digests. Synthetic ordinals are the only work
references; inputs and outputs contain no live task, board, origin, URL, host,
credential, token, image, service identifier, or wall-clock read.

## Exact input schema

Every input has exactly these keys. `lanes` is a two-element array in the fixed
order Lightpanda, Chromium. `placements.inflight` and `placements.ready` are the
only placement collections.

```json
{"capability_census_revision":"census-1","config_revision":"config-1","declared_assignment_count":0,"invalidation_events":[],"lanes":[{},{}],"now":1000,"placements":{"inflight":[],"ready":[]},"policy_revision":"policy-1","queue_revision":"queue-1","routing_revision":"routing-1"}
```

The ready/inflight union has exactly `declared_assignment_count` records and
unique contiguous ordinals `0..declared_assignment_count-1`. Arrays are in
ordinal order. Every placement has exactly:

```json
{"admission":{"policy_revision":"policy-1","verdict":"permit"},"assignment":{"backend":"lightpanda","capability_class":"browser-default","immutable_copy":{"backend":"lightpanda","capability_class":"browser-default","routing_revision":"routing-1","service_lane":"lightpanda"},"routing_revision":"routing-1","service_lane":"lightpanda"},"due_at":1000,"eligible_since":990,"fallback_target":"none","fence":{"claim_fence":1,"config_revision":"config-1","engine_owner":"owner-lightpanda","queue_revision":"queue-1","routing_epoch":1,"shard_id":"shard-lightpanda"},"lane":"lightpanda","ordinal":0,"priority":"monitor","work_class":"monitor"}
```

Chromium changes only the lane/backend/service-lane values and its queue-fence
identity. Closed enums are `work_class=monitor|detail`,
`priority=first_time|monitor|detail`, `lane/backend/service_lane=lightpanda|chromium`,
`admission.verdict=permit|defer|deny|violation`, and
`fallback_target=none|lightpanda|chromium`. A non-`none` fallback target is
evidence of `fallback_attempted`; it never authorizes dispatch. Only ready
placements participate in eligibility and arbitration.

Each lane has exactly:

```json
{"capacity":{"admitted":1,"current":1,"desired":1,"drain_started_at":0,"draining":false,"hard_max":4,"inflight":0,"last_scale_at":0,"running":0,"scale_down_step":1,"scale_up_step":1,"warm_floor":1},"declared":{"assignment_count":0,"eligible_ready_count":0,"inflight_count":0,"oldest_eligible_age":0,"ready_count":0},"lane":"lightpanda","queue_fence":{"claim_fence":1,"config_revision":"config-1","engine_owner":"owner-lightpanda","queue_revision":"queue-1","routing_epoch":1,"shard_id":"shard-lightpanda"},"service_state":"admitted","telemetry":{"error_budget_burn":0.0,"headroom_p05_ratio":0.5,"observed_at":1000,"queue_oldest_age":0,"resource_saturated":false,"utilization_p95_ratio":0.5},"zero_proof":null}
```

`service_state` is the single service model:
`admitted|unready|error|unsupported|full`. `zero_proof` is a required key and is
either `null` or exactly:

```json
{"assignment_count":0,"capability_census_revision":"census-1","complete":true,"completed_at":970,"config_revision":"config-1","eligible_ready_count":0,"inflight_count":0,"oldest_eligible_since":null,"policy_revision":"policy-1","queue_fence":{"claim_fence":1,"config_revision":"config-1","engine_owner":"owner-lightpanda","queue_revision":"queue-1","routing_epoch":1,"shard_id":"shard-lightpanda"},"queue_revision":"queue-1","ready_count":0,"routing_revision":"routing-1","started_at":70}
```

The proof matches all current top-level revisions and the containing lane's
complete queue fence. Its ready, inflight, assignment, eligible-ready, and
oldest-eligible facts exactly match the globally recomputed facts. Valid zero
requires `complete=true`, every count zero, and
`oldest_eligible_since=null`.

Each invalidation event has exactly:

```json
{"capability_census_revision":"census-1","config_revision":"config-1","event_at":970,"event_ordinal":0,"kind":"assignment_created","lane":"lightpanda","policy_revision":"policy-1","queue_revision":"queue-1","routing_revision":"routing-1","work_ordinal":0}
```

Event ordinals are unique contiguous source order and `kind` is exactly
`assignment_created|became_eligible`. A current-revision event for the lane at
or after proof completion invalidates the proof; equality invalidates.

## Audit and decision semantics

Before either lane decides, the evaluator globally audits ordinal conservation,
ready/inflight disjointness, lane and global declarations, capacity inflight,
immutable assignment, source/target lane attribution, queue-fence equality, and
cross-lane fence identity. Global loss/duplication and shared cross-lane faults
freeze every affected lane. Sibling state otherwise remains independent, and
no outcome reroutes work.

Fresh telemetry requires age `<=30s`, utilization `<=0.85`, headroom `>=0.15`,
error-budget burn `<=1.0`, and no saturation. Priority credits are
`first_time=300`, `monitor=60`, and `detail=0`; work aged at least `900s` uses
the absolute oldest-first override. Cooldown is complete at `60s`, drain at
`30s`, and zero proof requires a `900s` observation window and age `<=30s`.

Each lane result is exactly:

```json
{"decision":"claim","desired_concurrency":1,"lane":"lightpanda","reasons":[],"selected_item_index":0}
```

Precedence is `freeze > defer > claim`. Claims have a selected synthetic
ordinal and no reasons. Freeze/defer select nothing and contain all applicable
reasons of that outcome class, deduplicated and ASCII sorted. Malformed input
returns only `{"error":"invalid_input"}` without reflected input or parser
detail.

## Canonical corpus

The corpus envelope is exactly `{"cases":[],"format":"jobseek.browser-lanes.v1.conformance/v1"}`.
Cases remain in source order and each case is exactly
`{"expected":{},"id":"safe-id","input":{},"result_digest":"sha256"}`.
Object keys are ASCII sorted; placement/event arrays retain ordinal order;
numbers use the bounded non-exponent grammar; bytes are compact UTF-8 with one
final LF. A result digest hashes canonical expected-result bytes without an LF.
`scenarios.sha256` is exactly the lowercase SHA-256 of all raw `scenarios.json`
bytes, including its final LF, followed by one LF.

The generator performs an explicit audit on every run: all 27 closed reasons,
mandatory global-conservation/assignment/revision/service/fallback/proof
categories, multiple all-applicable cases, every expected result, every result
digest, the document entrypoint, and canonical corpus round-trip must pass
before bytes can be written.

Run from `apps/crawler/`:

```bash
uv run python contracts/browser/lanes/v1/tools/generate_corpus.py --audit
uv run python contracts/browser/lanes/v1/tools/generate_corpus.py --check
uv run pytest -q contracts/browser/lanes/v1/conformance/python/test_model.py
uv run ruff check contracts/browser/lanes/v1/README.md contracts/browser/lanes/v1/__init__.py contracts/browser/lanes/v1/tools/generate_corpus.py
uv run ruff format --check contracts/browser/lanes/v1/__init__.py contracts/browser/lanes/v1/tools/generate_corpus.py
uv run pyright contracts/browser/lanes/v1/__init__.py contracts/browser/lanes/v1/tools/generate_corpus.py
```
