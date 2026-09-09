# Dormant Lightpanda B1 adapter contract

`lightpandaadapter` is an inactive, fake-only Go seam over the generated
runtime-v1 browser messages. It grants no process, endpoint, origin, queue,
lease, persistence, deployment, or production-routing authority. There is no
Lightpanda client implementation in this slice.

## Fixed assignment and preflight order

The adapter accepts only an immutable assignment to backend `LIGHTPANDA`,
capability class `NAVIGATION_EVALUATION`, service lane `LIGHTPANDA`, and a
valid routing revision. Constant-time B1 cardinality checks run first, followed
by a 128 KiB serialized-input ceiling; no reflection walk or clone precedes
those bounds. Assignment validation then happens before capability
classification. The adapter validates a complete, unique capability list. The
adapter's fixed available set is `RENDER` plus `EVALUATE`; missing known
capabilities are returned once, sorted by enum number, before runner or origin
execution. Unknown, duplicate, or incomplete capability declarations are
invalid configuration.

The accepted B1 plan is deliberately smaller than runtime-v1:

- contract version `crawler.runtime/v1` and an absolute HTTP(S) target without
  userinfo or fragment;
- one `LOAD` navigation with a nonzero timeout no greater than 120 seconds,
  no headers or TLS override, and exactly one matching predeclared
  `navigation` origin operation at sequence 1;
- either render-only with capability `RENDER`, or render plus exactly one
  side-effect-free top-frame evaluation with capabilities `RENDER` and
  `EVALUATE`;
- no session, action, capture, interception, evaluation frame, evaluation
  origin effect, proxy, identity, persistence, pagination, or response-capture
  feature.

After those bounds, unknown protobuf fields are rejected recursively because
this inactive slice has no fleet evidence permitting forward fields to be
ignored. The complete `BrowserExecutionInput` is then cloned and fingerprinted
from deterministic protobuf bytes without normalization. The injected runner
can retrieve only further clones. Its opaque outcome constructors bind that
fingerprint automatically, preventing plan, assignment, caller, or wrong-task
mutation from changing the bound task.

## One-shot runner lifecycle

`Runner.Run` is called synchronously at most once. There is no retry, fallback,
goroutine wrapper, or sibling-backend selection. Its context deadline is the
earlier of the caller deadline and `NavigationPlan.timeout_ms`. The runner owns
its one-shot target lifecycle and must observe that context, complete disposal
even after cancellation, and return only after cleanup or process kill. A
runner reports an unproved cleanup through `NewRunnerCleanupFailure`; this
fail-closed result is evaluated before concurrent timeout or cancellation.

The adapter contains runner panics. It intentionally does not manufacture a
thread-based timeout around an uncooperative runner: a future real runner must
provide the independently supervised, bounded process lifecycle before this
seam can be activated.

## Output and privacy

A runner constructs exactly one opaque typed failure, cleanup failure, or raw
success. Constructors perform constant-time count and length gates before
copying, clone every retained slice and pointer, and automatically bind the
input fingerprint. Failure code/disposition pairs are closed. Success must
preserve evaluation count, identity, and plan order exactly. All identities
and limits are prevalidated before the first privacy call, so malformed later
values cannot cause a partially processed prefix to escape.

Every success must include an HTTP status in the closed range 100 through 599
and rendered HTML, because every admitted B1 plan requires `RENDER`. Missing
status or nil HTML fails closed before privacy. HTML is capped at 1 MiB and
mapped to complete inline runtime-v1 chunks of at most 64 KiB with per-chunk
and total SHA-256 values. Present non-nil empty HTML remains a complete empty
manifest.

Every evaluation passes exactly once through the mandatory injected
`EvaluationPrivacy`. The effective raw and sealed payload ceiling is
`min(EvaluationPlan.max_result_bytes, 65,536)`. The adapter contains privacy
panics, then independently checks the registered schema ID/version/encoding,
absence of unknown protobuf fields, JSON validity, payload size, lowercase
digest shape, and exact digest before cloning the envelope into
`EvaluationValue`. Canonicalization and structural redaction remain solely the
privacy boundary's responsibility; the adapter does not copy that
implementation.

Cleanup failure, cancellation, panic, binding mismatch, malformed output,
privacy rejection, or any limit failure returns one closed Lightpanda error
and discards all HTML and evaluation output.

The compact corpus at `fixtures/lightpanda_adapter/manifest.json` freezes the
cross-language assignment/capability/shape decision order. Go exercises it
through the adapter and Python evaluates the same runtime-v1 enums and closed
cases. Lifecycle, mutation, mapping, limits, and partial-output suppression are
covered by adversarial Go tests because those behaviors belong to this Go
adapter rather than the language-neutral wire.
