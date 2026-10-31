# Ordinary native whole-lane comparison

Run the current default Go ordinary worker against the retained Python HTTP and
browser workers on identical, isolated input. This extends the measurement scope
beyond the earlier B0 producer/supervisor/executor composition. It does not change
production ownership or contact a real career site.

The input contains 536 deterministic postings across four effective profiles:
a full 500-row rich API-sniffer chunk; direct DOM discovery and JSON-LD detail processing;
rendered DOM discovery and rendered JSON-LD detail processing; and pre-existing
direct JSON-LD details. Normal, delayed and large responses exercise the same
bytes in each arm. Rendered responses require JavaScript execution. The existing
production-cardinality synthetic taxonomy is shared, with a fixed supplied USD
rate. The workload SHA and exact source revision identify the generated input.

Both compositions have aggregate discovery capacity four, monitor capacity two
and 3.5 CPU units for workers plus browser/renderer. The native worker retains
its production 1 GiB/512 MiB Go memory limits; the renderer retains its 1 GiB
limit. Legacy HTTP/browser workers split the discovery/monitor capacities.
All containers have swap disabled. These are bounded comparison settings,
not a request to increase production capacity.

Each arm starts fresh PostgreSQL, Redis, Typesense and an isolated fixture/S3
PUT sink. Native reaper, exporter and drain run in both arms. Native processing
uses `go-ordinary-worker` in default ownership mode and self-hosted Lightpanda;
the comparator alone uses the explicitly retained legacy target. It explicitly selects Python for JSON-LD, DOM HTTP/parse and
enrichment; installed Go-bridge defaults cannot silently replace its extraction.
Reaper, exporter and drain remain common native actors in both measured lanes.
Earlier results without these comparator settings cover the retained mixed
worker, rather than a Python extraction baseline. Bootstrap and
inspection Python runs in separate fixture/tooling containers. No container has
the Docker socket, host networking, published ports or production credentials.
Both Docker networks are internal. Automatic address pools exclude the fixed
renderer and origin-fixture addresses, so bootstrap cannot claim them. The driver rejects all database/Redis
endpoints except its literal disposable service addresses before connecting.

Bootstrap uses future canonical deadlines and the same throttle key in SQL and
the board cache, before the common feed window. All four generated board
configurations must pass Go admission with their actual HTTP/browser lanes.
Rendered navigation verifies TLS against the fixture CA. The isolated legacy
browser imports that CA into its private ephemeral NSS database, following
[Chromium's Linux trust contract](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/cert_management.md).
Its browser binaries are readable by the configured unprivileged UID; certificate
setup uses the existing container CPU/memory limits and counts as startup work.
The public CA bundle uses mode `0444`, so the isolated browser child can verify
HTTPS origins; private keys remain unreadable to that child. The host PKI
directory stays private. The renderer key uses mode `0400`, and its temporary
filesystem belongs to the isolated browser child UID, matching the service startup
contract.
The description sink also serves HTTPS with the fixture service identity. Actors
and the report reader trust its CA through a read-only bundle, preserving the
Go drain's required HTTPS endpoint contract.

Three pairs alternate order. Both arms must finish the complete canonical job,
description upload and Typesense work. Independent SQL, uploaded-body hashes
and Typesense reads qualify effects; counters are not the output oracle.
Canonical results, excluding generated posting IDs and timing, must hash equally
across every arm. A common 35-second retention period follows completion.
Cgroup CPU, synchronized working-set samples and retained memory include every
worker, renderer, reaper, exporter, drain, PostgreSQL, Redis, Typesense and fixture
container. Per-service lifetime CPU and memory peaks additionally expose startup.
Role-weighted cost uses the same public October 30 catalogue snapshot as the
production observer; the fixture remains in raw resource totals but has no
production-role price. The allocation model is not billed savings or a model of
architecture-specific performance. Sampled memory peaks are not absolute peaks.

The workflow builds immutable images at the exact checked-out revision and
records their IDs, architecture and runtime contract. It retains a compact
numeric report for one day. Each arm removes and verifies its own containers,
networks and volumes; temporary credentials are deleted when the controller
exits. No raw upstream/job content or subprocess environment is an artifact.
Missing output, unequal canonical results, OOM/restarts, incomplete sampling or
failed cleanup fail the run. A failed arm stops subsequent arms.
Incomplete work reports numeric posting/title/failure/upload counts for each
effective profile and the successful-monitor count, plus bounded service state
and worker diagnostic counters. Per-route monitor outcomes and upstream request
counts distinguish unclaimed work from attempted rendering. Native diagnostic
text is parsed using the worker's bounded error format; arbitrary messages and
request identities never enter failure annotations. An incomplete arm still
fails and verifies cleanup.
When the sole rendered monitor fails before producing inventory, an existing
typed renderer stdio request may diagnose that already-failed isolated arm. It
uses the same container, privilege drop, limits and fixed fixture origin, emits
only result/error codes and bounded categories, and is excluded from measurement.
No diagnostic probe can turn an incomplete arm into success.
Compact success measurements and bounded failure diagnostics also appear in CI
annotations, so the required CI reader can inspect them when raw logs are unavailable.

Local checks from `apps/crawler`:

```sh
uv run --frozen pytest admission/native_lane -q
uv run --frozen ruff check admission/native_lane
uv run --frozen ruff format --check admission/native_lane
```

The full composition runs in `crawler-native-whole-lane.yml` on Linux with
Docker and cgroup v2. Do not mistake a fixture pass for complete enabled-fleet
coverage, live provider recovery, production freshness or final migration
acceptance. Keep the independent deployed-fleet evidence and original gates in
[the continuation plan](../../../../docs/27-go-lightpanda-continuation-plan.md).

The workflow also runs the same six-arm accounting with
`NATIVE_LANE_INPUT_MODE=captured`. This corpus contains 72 distinct saved HTTP
detail responses across JSON-LD and DOM, seven source hosts and three recorded
configurations. Every decompressed page retains its captured SHA-256. The
workspace saves decoded HTML rather than compressed HTTP wire bytes. The
original full response headers and network timings were not retained; this
does not establish live publisher-policy or network-performance equivalence. The
configuration record is bound by run time and exact reproduction of all saved
extraction fields; an immutable original configuration snapshot is unavailable.
Corpus qualification counts, including rejected candidates, are in
`corpus/provenance.json`. Originals are never contacted during comparison.
Native direct HTTP uses embedded production trust roots rather than the system
CA store. Both captured arms therefore use the same explicit `skip_ssl` fixture
exception as generated direct requests. The recorded extraction configuration
and page bytes remain intact; the workload hash includes this transport
override. This does not measure production certificate-verification cost.

Captured work starts from scheduled details; there is no captured monitor or
rendered-dependency claim. All worker, renderer and storage actors remain in
the resource scope. Discovery, monitor scheduling and positive salary cases
retain their independent generated scenario. The captured scenario requires
every input title, completed SQL/detail ownership, complete uploaded description
bytes, matching canonical SQL across arms, Typesense convergence, publisher
policy, future-work disposition and cleanup. It reports hashed request identities.
The workflow runs both scenarios on native AMD64 and ARM64 runners. Each run
still uses one machine architecture: these results measure each architecture
without emulation and do not reproduce cross-host networking in the mixed
x86 crawler and ARM renderer topology of production. Artifact names include
both architecture and input mode so reports cannot overwrite each other.

To qualify another existing archive without contacting sources:

```sh
DOM_GO_PARSE_ENABLED=0 uv run --frozen python -m admission.native_lane.captured_inputs \
  --workspace /path/to/existing/.workspace --destination /path/to/new/private-inputs
```

The command refuses an existing destination, writes private files, reports only
counts/hashes, and never edits source artifacts. Unsupported browser captures,
missing request/configuration provenance, incomplete output, changed extraction
and duplicates are counted separately. Qualifying inputs alone is not a
whole-lane resource result. Keep only inputs for an active comparison or useful
regression fixture; delete consumed private copies after freezing the corpus.

After a failed native arm with no rendered inventory, the controller stops
measurement and probes the fixed fixture through the actual resident renderer
using its pinned mTLS identity, worker navigation options, document-actions
envelope and isolated child. The runner receives only the disposable fixture's
client identity. Typed stage/outcome/error codes and HTML byte counts are emitted;
HTML, certificates, credentials and arbitrary errors are never printed. The
existing raw stdio probe remains a separate diagnostic. Neither counts toward
completion or efficiency, and all disposable containers are still removed.

The disposable PKI supplies a public Go HTTPS trust bundle and a separate
fixture root at `/etc/ssl/certs/native-lane-fixture.crt`, the directory Lightpanda
scans before bundle fallback. Both public files are readable by the isolated
browser UID; private keys retain their restricted modes and the host PKI
directory remains private. Public file modes are set explicitly after creation:
the measurement runner's `umask 077` otherwise reduces requested `0444` to `0400`.
The permission regression runs with both `022` and `077`. TLS verification stays
enabled in the renderer.

The generated salary phrase uses `$100,000-$120,000 per year`, which the retained
Python oracle and Go both parse as USD100000–120000 yearly. The fixture regression
checks the oracle on every distinct description; the SQL expectation remains
unchanged. A literal `USD 100000-120000 yearly` returns no salary in both engines
and is unsuitable for this positive-case workload.

The search gate waits for the canonical title and company as well as complete
document coverage. Preseeded jobs can already exist in Typesense while CDC
updates are still pending. Stale fields must converge within the original
collection deadline; persistent mismatches emit only bounded counts and fail.

The native monitor gate requires the Redis due score to match canonical SQL
within 1 ms. The unchanged Python comparator records SQL success at database
`now()` and schedules Redis at its later pipeline clock. For that arm, SQL must
retain the full configured day after success, and Redis must fall between that
SQL due time and the completed observation plus the same day. Missing, past,
early, over-delayed or changed-interval schedules fail. This uses the original
clock contract, without adding a timing allowance or changing either runtime.
