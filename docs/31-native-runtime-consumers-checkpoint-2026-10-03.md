# Native runtime consumer checkpoint — 2026-10-03

The delivery goal remains the full production migration: every enabled profile
must run through Go HTTP/API routes or self-hosted Lightpanda, followed by
production Python, Playwright and Chromium retirement after supported reversal
and the rollback window. Offline Python tooling may remain isolated.

This candidate removes four Python healthcheck invocations from the mandatory
Compose worker/browser services. Their installed Go executable checks the fixed
loopback health endpoints without loading worker credentials or enrichment
assets. The actual executable passes 200/204 checks and refuses failed endpoints,
redirects and invalid ports. This removes probes; those four service processes
still execute Python until their remaining effective profiles migrate.

Production v0.13.910, source `c7b1dcf2c4d25a2677aaf70073928a9e940fd441`,
has 2,570 Greenhouse native owners at routing epoch 151. The 21:07 UTC readback
verified all 2,570 SQL/Redis members and 345 completed current-epoch deadlines;
it recorded 343 successful tasks, two failed tasks, three timeouts, one
unacknowledged task and one claim error. HTTP transport/execution counters were
zero errors, demonstrating that these counters alone do not establish task
success. Five boards subsequently had `native_processing_failed` outcomes.
All eight HTTP readiness endpoints still passed. These failures require diagnosis
before cohort expansion; no claim of full-service freshness or cost parity is made.

[PR #10246](https://github.com/colophon-group/jobseek/pull/10246) connects native
Ashby/Lever rich monitors. Candidate admission covers 934 Ashby and 194 Lever
boards; two detail profiles remain excluded. Required CI, installed-image parity
and the actual Crawler Deploy Gate passed at
`64838e68c5702cf34f8a259fcc80170762af3a96`; ARM64 admission is pending at this
checkpoint. These boards have not yet acquired production native ownership.

An independent bounded worker improvement reuses the already validated immutable
ownership document while freshly checking its exact SQL active identity, routing
epoch, Redis projection and canonical board configuration. Re-decoding a
representative 2,570-member plan cost 137 ms and allocated 42 MB per operation
on an Apple M2 with Go 1.26.0. Removing that recurring work addresses demonstrated
fleet overhead; it does not yet establish the cause or resolution of processing
failures. SQL pool, timeout, retries, lease barriers and cutover authority remain
their existing contracts.

Continue by completing these exact-head checks, merging the bounded slices and
building immutable images. Use the existing complete quiesced deployment and
ownership transitions, verify natural jobs and failures, then admit fresh
Ashby/Lever cohorts. Continue remaining provider/detail/browser profiles and
mandatory Python startup, migration, maintenance and deployment consumers.
Retire legacy production execution only when coverage, canonical effects,
publisher policy, freshness, queue conservation, whole-service resources and
supported cold reversal/window are proven. Preserve every enabled board.
