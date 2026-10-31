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
the comparator alone uses the explicitly retained legacy target. Bootstrap and
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
