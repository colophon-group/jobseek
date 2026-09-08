# Concurrent worker capacity checkpoint

Issue: #7948. This is a non-production continuation gate for the admitted
HTTP/sitemap slice. It is not a crawler-wide ROI, Lightpanda, deployment, or
production-readiness benchmark.

## Question

Under the same fixed process-tree resource envelope, does one warm Go worker
sustain materially more correct sitemap jobs than the equivalent warm Python
worker shape?

This is deliberately a worker-design comparison, not a language-only
microbenchmark. The Go arm may replace Python's dequeue-then-wait scheduling
with origin-aware dispatch; that redesign is part of the migration hypothesis
and must be reported as such. Results cannot attribute a gain to Go syntax or
runtime alone.

The earlier #7936 evidence used one fresh process per invocation. It remains
useful component evidence, but cannot answer this worker-level question.

At frozen `origin/main` `fb6117b06a3008cdac76adc258327b2f220085f8`, one
production HTTP worker has one vCPU and 1 GiB, 20 long-lived discovery
coroutines, a five-monitor cap, and one shared
`httpx` client capped at 20 active and 10 idle connections. Three replicas run
in production. The primary comparison is one process against one process; a
separate three-process-versus-one-process consolidation experiment may follow
but must not be presented as a language-only result.

The 20 discovery coroutines are dequeue/claim loops, not 20 monitor service
permits: a claimed monitor waits behind the five-permit semaphore. The
benchmark concurrency ladder controls executor service permits. Only level 5
matches the present monitor cap; higher levels measure capacity headroom.

## Frozen comparison boundary

Both implementations must:

- stay alive for warm-up and every measured batch;
- run Python with the frozen production 3.13.15 interpreter and locked
  dependency versions, and record the exact Go toolchain and binary identity;
- use a bounded feeder, bounded result sink, fixed worker count, and a shared
  process-level HTTP connection pool;
- enforce the same global active-request cap, total open-connection cap,
  global idle-connection cap, and idle expiry;
- apply the same per-origin concurrency cap and per-job deadline;
- consume the same ordered, hashed job manifest and deterministic fixture
  responses;
- emit one terminal result per accepted job, including typed failures;
- run sequentially on the same Linux/amd64 host under one vCPU, 1 GiB
  memory+swap, 128 PIDs, and the same file-descriptor limit;
- receive no repository data, environment file, credentials, Redis, Postgres,
  production volume, production network, or public egress.

The fixture runs outside the measured cgroup in the same Docker network
namespace, with public networking disabled. Distinct loopback listeners on
distinct ports represent distinct origins while remaining hermetic. Its request
transcript is authoritative for admitted requests, wire attempts, status order,
and response bytes.

Current Python/httpx has a global idle bound but no configurable per-origin
idle bound. Go's additional per-host idle bound is therefore a disclosed
conservative safety constraint, not a symmetric comparison input; the corpus
must demonstrate that it is non-binding for accepted capacity results.

## Corpus and execution

Freeze a sanitized corpus with large `urlset`, one-level `sitemapindex`,
deterministic root retry, and typed terminal-failure cases. Unsupported syntax
and the known Next live-origin incompatibility must be listed as exclusions,
not silently counted as failures or cheap successes.

The common parity cohort excludes child retries, nested indexes, redirects,
root rediscovery after 404, non-retryable 4xx, or malformed XML, TDM, proxies,
skip-TLS, byte-versus-character limit boundaries, and non-literal Python filter
or query syntax. Root retry exhaustion is a correctness case, but its timing is
reported separately because current Python jitter and Go deterministic waits
are different policies.

Predeclare a concurrency ladder through saturation. The initial ladder is 1,
5, 20, and 50, plus one larger bounded c20 conservation stress batch. The
stress batch is not an open-loop offered-arrival test. Use symmetric dependency
preload and warm-up, alternate implementation order, and run at least five
independent repetitions per level. Retry-policy-dominated jobs are reported
separately from parser/worker capacity.

## Required evidence

For every job, preserve URL count and digest, decoded bytes, explicit requests,
wire attempts, retry schedule, and typed outcome. For every run, record:

- completed and successful jobs per second;
- queue, service, and end-to-end latency p50/p95/p99;
- process-tree CPU seconds per successful job;
- steady and peak process-tree RSS;
- maximum queued and in-flight work;
- open and idle connections, file descriptors, requests, response bytes,
  retries, timeouts, panics/crashes, OOMs, and unfinished jobs.

Raw observations, source and image identities, commands, manifests, fixture
transcripts, and summary calculations must be retained. Startup is reported
separately rather than hidden inside steady-state measurements.

## Decision rule

Correctness, boundedness, and conservation are hard gates. There may be no
unexplained request amplification and no lost or duplicate terminal result.

The continuation threshold is evaluated at c5, the current production monitor
service shape: a lower 95% confidence bound of at least 1.20x saturated
successful executor cycles per identical resource envelope, with no
correctness, error-rate, steady-RSS, or p99 regression. The other concurrency
levels are diagnostic headroom and saturation observations. A symmetric 0.85x
planning rate may be reported, but it is not measured sustainable capacity and
is not part of the continuation gate.

An open-loop arrival-rate stability test would require the production queue's
temporal per-domain pacing and explicit latency/error SLOs. Adding a synthetic
version here would overstate what this hermetic executor can prove; defer it to
the production-cohort checkpoint.

Passing permits the bounded Go migration to continue. It does not authorize a
production cohort, production-throughput claim, or a fleet-cost claim. The
production queue applies temporal per-domain start delays that this executor
test deliberately excludes. Failing is a valid result and stops expansion
until the design or premise changes.

## Deferred until this gate passes

Queue protocol v2, Redis ownership and leases, Postgres mutations, publisher
fencing, TDM/proxy parity, complete production telemetry, cohort routing,
rollback, and Lightpanda fleet capacity remain required before production.
They are deliberately not prerequisites for measuring the worker hypothesis,
and their missing overhead must not be attributed as a Go saving.
