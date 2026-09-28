# Shared Go job enrichment

The resident `job-enrichment` binary owns occupation, seniority, technology,
experience matching and description HTML normalization for monitor and detail
processing. It loads the same read-only
CSV taxonomy once, returns taxonomy slugs and experience bounds, and leaves
database ID resolution to the caller. `Matcher` is directly reusable by the future native worker.

Compose selects `JOB_ENRICHMENT_ENGINE=go`. Outside Compose the default is
`python`; explicitly selecting `python` is the cold rollback path. There is
no same-task Python fallback. Unknown engine names fail. The worker and
remaining CPU stages (language, location, salary), scheduling
and persistence still run in Python. This is not the full #7966 completion.

Each Python worker process creates one child on first use. A lock serializes
newline JSON requests; forked processes discard inherited pipe descriptors
without signalling the parent's child. Each request has a ten-second deadline,
a request bound below 16 MiB, taxonomy/experience response bound of 1 MiB,
and sequence check. HTML responses have a separate sixfold request-size bound
(approximately 96 MiB) to preserve delimiter/entity expansion without truncation.
The Go JSONL encoder emits UTF-8 without optional HTML-safe JSON escaping;
this pipe is never embedded in HTML. The handshake retains its 1 MiB bound.
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
`implementation="go-job-enrichment"`, and the bounded capabilities
`occupation_seniority` / `technology` / `experience` / `normalize_html`.
These executions add no origin traffic.

## Description HTML normalization

All shared `normalize_description_html` call sites (rich monitor results,
detail extraction, and CPU processing) dispatch through the same resident
process when Go is selected. Go owns the entire parse, subtree removal, tag
unwrapping, attribute cleanup and serialization. Python retains the offline
oracle and explicit cold reversal; failures never call it automatically.
The pinned Go parser rejects an open-element stack above 512 nodes; the IPC
request size and ten-second deadline remain enforced. Rejection fails the
task and reaps the child, without truncating content or returning empty success.

The HTML5 document parse uses the legacy scripting-disabled/body context.
Serialization preserves quotes in text, comments, formatting reconstruction,
table foster parenting, escaped-markup detection with Python Unicode boundaries,
Python's numeric-reference exclusions, and NBSP replacement **after** serialized
whitespace trimming. Empty unknown elements, including their attributes, are
retained exactly as the existing Lexbor `unwrap` behavior requires. This keeps
canonical description bytes and R2 hashes stable.

`testdata/generate_html.py` freezes 8,567 Python/Lexbor cases: every HTML5 named
entity, numeric controls/noncharacters, all tag policies, Unicode boundaries,
and 2,000 deterministic malformed-markup combinations. Go race tests, the
shared public Python bridge and installed-image offline verification exercise
this oracle. Bridge tests also cover expanded responses above 1 MiB, request
and response bounds, reaping and mixed operations in one resident process.

`testdata/replay_stored.py --scope normalize_html` compares the full normalized
bytes on stored production content. This is an offline stage measurement;
it does not establish whole-lane production CPU, RAM, density or cost.

## Experience requirements

`experience` preserves the three ordered Python matching passes (mixed units,
forward and reversed wording), highest accepted minimum, equal-minimum tie
behavior, Unicode number/space classes, the 60-character preceding-context
exclusions, and the 30-year ceiling. Integer tenths preserve decimal half-up
month conversion without floating-point rounding drift. Forward expressions
are anchored at eligible numeric starts; optional prefix positions still
define the false-positive context window.

`testdata/generate_experience.py` freezes the retained Python expressions and
1,967 oracle cases, including existing regression inputs and numeric boundary,
multilingual-unit and prefix/context combinations. Installed-image CI and the
shared bridge test exercise them through the same resident process.
