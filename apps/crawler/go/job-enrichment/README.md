# Shared Go job enrichment

The resident `job-enrichment` binary owns occupation, seniority, technology,
experience matching, salary extraction, language detection and description HTML normalization for monitor and detail
processing. It loads the same read-only
CSV taxonomy once, returns taxonomy slugs and experience bounds, and leaves
database ID resolution to the caller. `Matcher` is directly reusable by the future native worker.

Compose selects `JOB_ENRICHMENT_ENGINE=go`. Outside Compose the default is
`python`; explicitly selecting `python` is the cold rollback path. There is
no same-task Python fallback. Unknown engine names fail. The worker and
location index loading/backfill, scheduling
and persistence still run in Python. Location matching runs in a separate Go resident. This is not the full #7966 completion.

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
`occupation_seniority` / `technology` / `experience` / `normalize_html` /
`language` / `all_languages` / `salary` / `location_resolve` /
`location_ancestors` / `location_display`.
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


## Language detection

Go owns both shared primary detection and ordered multilingual chunk detection
when the enrichment engine is Go. The exact compressed `lid.176.ftz` model
already used by locked fast-langdetect 1.0.1 is embedded (digest checked) and
loaded once. Pure Go decodes its quantized vectors and hierarchical softmax;
no Python/C++ binding, external model download or network is required.
Model and adapted-code provenance/license notices are in `models/` and shipped
in the image.

Preserved contracts include regex tag stripping without entity unescaping,
Unicode codepoint limits/trimming, the 500-character primary/chunk boundary,
legacy hard-cut character skip, 80-character minimum chunk length,
fast-langdetect's **80-character prediction limit** after LF replacement and
before uppercase normalization, signed-byte UTF-8 hashing, EOS handling,
quantized float32 accumulation, the 0.3 confidence boundary, the 15% ratio
using all valid chunks as denominator, and insertion order. Scores can differ
by floating-point rounding; exact public primary/multilingual outputs match.
Runtime/model/IPC errors fail explicitly; they never become empty successful
results or automatically call the Python model. Python is retained lazily for
explicit cold reversal and offline comparison.

The frozen oracle has 1,347 synthetic prediction/description cases across
19 languages, mixed/minority descriptions, confidence/length boundaries,
uppercase and UTF-8 inputs, plus 1,982 Unicode preparation checks. Go tests
check predicted labels/scores (2e-6 tolerance), public results, and corrupted
model rejection. Shared bridge tests and installed-image offline CI check
public output and resident reuse. `testdata/replay_stored.py --scope language`
compares private actual descriptions and measures this stage alone.


## Salary extraction (v0.13.900)

Go owns all shared salary APIs (`extract_salary`, `extract_salary_unified`,
`parse_salary_text`) and the CPU five-field result, including annual EUR
conversion with caller-supplied exchange rates. USD/CAD/AUD/NZD/SGD/HKD/BRL/MXN,
EUR, GBP, CHF and PLN/CZK/SEK/DKK/HUF/RON/BGN preserve existing ordered
patterns, number and period rules, context windows, gross/net and perk policies,
mojibake repair, hourly cents, range/single deduplication and group tie order.
Existing quirks (including reversed CHF ranges and whole-unit dollar ranges
with hourly labels) are preserved. EUR annualization retains 2080 hours/year,
12 months/year, float64 operations and ties-to-even rounding.

The pinned pure-Go regexp2 v2.8.0 engine implements existing lookarounds, with
explicit Python Unicode word/digit/space classes and ignore-case preparation.
Patterns have a 500 ms match timeout, bounded backtracking stack and cache;
a scan also has a five-second deadline beneath the ten-second IPC deadline.
An overflow beyond signed 64-bit transport, regex limit or IPC error fails
explicitly and reaps the child. No partial or empty success/Python fallback is
returned. Existing database integer constraints remain enforced by persistence.
The Python implementation is retained only for explicit engine reversal and
protected offline comparisons.

`testdata/generate_salary.py` freezes all existing regression strings, currency
and period thresholds, context/magnitude failures, Unicode/mojibake inputs,
aggregation and EUR rate behavior. Its 2,664 cases include 902 positive cases
and all 18 supported currencies. Go race tests, all four public bridge APIs
(with the Python oracles replaced by rejecting stubs), and installed-image
verification consume the same frozen results. CI runs Go race/vet/mod/format
checks separately from the Python binary-build fixture, avoiding nested Go
suite work under pytest's per-test deadline.

`testdata/replay_stored.py --scope salary` compares all salary ranges,
unification, public parsed fields and EUR results on protected stored bytes.
The salary slice was merged in [#10177](https://github.com/colophon-group/jobseek/pull/10177).
Use the [current migration checkpoint](../../../../docs/28-go-location-resolver-checkpoint-2026-09-30.md)
for deployment and ownership status.


## Location matching (candidate v0.13.901)

The separate `location-resolver` resident owns all public free-form location
resolution, ancestor traversal and display lookups when `JOB_ENRICHMENT_ENGINE`
is Go. It uses a pure-Go SQLite reader over a private mode-0700 directory and
mode-0600 index. The transition loader and batched non-core-name backfill still
use the existing worker PostgreSQL pool, with its 500-key query chunks and
unchanged queries. No additional PostgreSQL connection is opened by Go. The
native worker migration must replace that loader/backfill before Python retirement.

Preserved semantics include aliases/country/state collisions, accent and name
variants, type hints, semicolon/multi-city ordering and deduplication, language
and population disambiguation, compound recursion, ancestor-depth limits,
miss samples and negative-cache backfill behavior. Equal population/context
ties retain the previous CPython positive-integer set order. Regex classes share
the bounded Python-compatible matcher used by salary; each operation has a
five-second deadline beneath the IPC deadline. Inputs fail on the IPC
bounds or deadline rather than truncating or falling back to Python.

The index survives child-process restarts. Forks discard inherited pipes and
open their own resident without terminating the parent or deleting its index.
The monotone negative cache is sent on growth, replacement or restart rather
than copied/sorted for every posting. Initial index creation and backfill still
write through the parent loader; the Go helper opens the index read-only.

`testdata/generate_location.py` freezes existing Python regressions without
publisher traffic. Go and public bridge tests cover 322 resolution cases;
installed-image CI exercises the public API with no network and a read-only
image plus bounded scratch space. A private replay of the current 37,526-entry,
7,925,760-byte production index matched 1,024 resolutions and 128 display/ancestor
lookups exactly; the raw index and samples remain outside Git. Additional tests cover backfill visibility,
negative-cache rehydration, cycles, display names, missing-index failure and
fork ownership. This candidate has not yet been deployed or measured against
an actual-workload whole-lane resource baseline.


`testdata/replay_location.py SNAPSHOT --binary BINARY --output PRIVATE_JSON`
replays a protected actual taxonomy snapshot without network access. The snapshot
contains a serialized SQLite index and previously frozen Python output; require
mode 0600 and never commit it. Replay timing covers this stage only. Dependency
notices in `licenses/` are reproducible with `testdata/export_licenses.py` from
the verified pinned module cache and ship in both crawler images.
