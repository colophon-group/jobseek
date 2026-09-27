# Shared Go job classification

The resident `job-enrichment` binary owns occupation, seniority and technology
matching for the monitor and detail CPU helpers. It loads the same read-only
CSV taxonomy once, returns slugs, and leaves database ID resolution to the
caller. `Matcher` is directly reusable by the future native worker.

Compose selects `JOB_ENRICHMENT_ENGINE=go`. Outside Compose the default is
`python`; explicitly selecting `python` is the cold rollback path. There is
no same-task Python fallback. Unknown engine names fail. The worker and
remaining CPU stages (HTML, language, location, salary, experience), scheduling
and persistence still run in Python. This is not the full #7966 completion.

Each Python worker process creates one child on first use. A lock serializes
newline JSON requests; forked processes discard inherited pipe descriptors
without signalling the parent's child. Each request has a ten-second deadline,
a request bound below 16 MiB, response bound of 1 MiB, and sequence check.
Errors reap the child before another call may replace it. Normal EOF exits the
child. No network, publisher fetch, database access or file writes occur.

Behavior preserved: CSV insertion-order ties and alias overrides, longest
boundary occupation match, token fallback, multilingual NFKD normalization,
ordered seniority rules, internship overrides, technology boundaries/case
flags, missing IDs, ID zero, deduplication, and first available title match.

## Verification

From this module: `go test -race ./...`, `go vet ./...`, `go mod tidy -diff`.
The Python bridge lifecycle tests run the real binary. Installed-image CI runs
`testdata/verify_installed.py` with network disabled and the image read-only.
The frozen Python oracle contains 1,568 titles and 2,056 technology inputs,
including all current aliases, locales, and technology literals. Regenerate it
from the repository root using `PYTHONPATH=apps/crawler python
apps/crawler/go/job-enrichment/testdata/generate_python.py`.

`testdata/replay_stored.py SAMPLE --data-dir apps/crawler/data --binary BINARY`
compares stored production content offline. SAMPLE is a mode-0600 JSON array
with `titles`, `employment_type`, `html`, `board_id`, and `locale`; never commit
raw posting content. `--engine python` and `--engine go --rounds 10` measure
the exact same inputs separately. CPU includes the resident child and Python
bridge; RSS maxima are reported separately. This measures classification only,
not whole-lane RAM, density or cost.

Production instrumentation uses `stage="enrichment"`,
`implementation="go-job-enrichment"`, and the two bounded capabilities
`occupation_seniority` / `technology`. These executions add no origin traffic.
