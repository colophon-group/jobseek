# BrowserExecutor assignment boundary v1

This document freezes a dormant contract boundary for the shared Go
`BrowserExecutor`. It activates no runtime, service, queue, route, origin
request, or persistence authority.

## Pre-origin assignment

Each browser `ExecutionRequest` contains one `BrowserExecutionInput` with one
`BrowserPlan` and one `BrowserAssignment`. Before invoking a provider or
dispatching any origin operation, the owner must validate and durably bind:

- one non-unspecified `BrowserBackend`;
- the capability class derived from the complete, unique, non-unspecified plan
  capability set;
- the matching non-unspecified `BrowserServiceLane`; and
- an ASCII routing revision matching
  `^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`.

Capability classes are backend-neutral. `NAVIGATION_EVALUATION` covers only
render/evaluate plans. `INTERACTION_CAPTURE` is selected when actions,
pagination, response capture, or interception is required and no
identity/transport capability is present. `IDENTITY_TRANSPORT` is selected
when frames, persistent sessions, headful identity, proxy, or transport
overrides are required. Lightpanda and Chromium may each own any class after
external frozen capability evidence; the class does not imply a backend.

The backend does imply an isolated service lane: Lightpanda maps only to the
Lightpanda lane and Chromium maps only to the Chromium lane. Those lane values
are the future queue/capacity identity. Actual queues, cgroups, resource
limits, and services are outside this contract slice.

## Exactly one provider

After preflight, one provider may be invoked once. Its identity and every
`BrowserResult.backend` must equal the bound assignment. Same-backend retries,
cross-backend retries, and automatic fallback are forbidden within one task.
Transport retry belongs below the provider invocation only when it cannot
repeat semantic origin work and still preserves the same provider identity.

If the selected provider does not declare every required plan capability,
preflight returns `BrowserUnsupported` before provider or origin execution.
The unsupported capability set is exactly the missing set. Unsupported and
error results contain no partial authoritative HTML, action, capture, or
evaluation output. Provider errors remain typed by `RuntimeError`; they never
select the sibling backend.

## Lifecycle and retirement

Lightpanda and Chromium are separate first-class service lanes whenever frozen
capability routing assigns them demand. Dual-engine operation may remain the
steady state. Removing Chromium is an external fleet decision permitted only
after #7966 proves zero enabled Chromium assignments and its removal/rollback
gate passes; this contract never infers retirement from idleness or an
unsupported result.

The shared fixture in `fixtures/browser_executor/manifest.json` is synthetic,
canonical, and network-free. Independent Python and Go evaluators hard-code its
case registry and must produce identical closed results for every case.
