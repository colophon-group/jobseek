# Daily labelled-postings routine

Scheduled Claude Code routine that samples job postings, labels them via
Claude Code subagents, and uploads a gold dataset to a public HuggingFace
dataset repo (`viktoroo/jobseek-postings-labelled`). The dataset is the
substrate for training a better structured-information extractor to
replace the current crawler heuristics.

## Why Claude Code and not the API

The routine runs entirely inside a Claude Code session. The orchestrator is
the session itself (Opus); subagents are invoked via the `Agent` tool on
Sonnet. No direct Anthropic API calls — that's the prod model's job, after
it's trained on this dataset.

## Architecture

```
Claude Code session (Opus orchestrator)
  ├─ Reads .claude/commands/jobseek-label-daily.md (its playbook)
  ├─ Shell-calls `labeller <subcommand>` for deterministic steps
  │    ├─ sample       — diverse per-company selection from the last 24h
  │    ├─ prepare      — load raw HTML, normalize, compute blocks
  │    ├─ render-task  — Jinja-render a task-input markdown file
  │    ├─ validate     — JSON-schema + custom rules (block-id coverage, etc.)
  │    ├─ merge        — assemble per-subagent outputs into one posting.json
  │    ├─ canonicalize — rule-based free-text → taxonomy IDs (sidecar)
  │    └─ upload       — push samples/ + canonical/ to HF
  │
  └─ `Agent` tool invocations for every LLM step (Sonnet)
       ├─ jobseek-labeller-splitter          Pass 1: sections
       ├─ jobseek-labeller-extract-company   Pass 2: per-section fields
       ├─ jobseek-labeller-extract-team
       ├─ jobseek-labeller-extract-role
       ├─ jobseek-labeller-extract-requirements
       ├─ jobseek-labeller-extract-preferred
       ├─ jobseek-labeller-extract-benefits
       ├─ jobseek-labeller-extract-application
       └─ jobseek-labeller-extract-globals    Pass 3: cross-section fields
```

### The "orchestrator never reads the subagent prompt" invariant

Subagent system prompts live in `.claude/agents/jobseek-labeller-*.md`.
Claude Code loads them from disk; the orchestrator's context never
contains them. The orchestrator passes variables to a subagent by
handing it a **rendered task-input file path**:

```
orchestrator:
  1. bash$ labeller render-task --task split_sections \
              --input  _runs/.../<id>/input.json \
              --out    _runs/.../<id>/split-in.md
  2. Agent(
       subagent_type="jobseek-labeller-splitter",
       prompt="input=_runs/.../<id>/split-in.md output=_runs/.../<id>/split-out.json",
       model="sonnet"
     )
```

The Jinja template `prompts/tasks/split_sections.md.j2` owns the variable-
insertion and phrasing; the subagent definition owns the task contract and
output schema; the orchestrator owns only the workflow and paths.

### Retry protocol

After every `Agent` invocation:

1. `labeller validate --kind <kind> --file <output_path>` runs schema +
   custom checks (block-ID coverage, overlap, enum membership, etc.).
2. If it fails, the orchestrator renders a new input file with the
   validator's error appended to a `## Previous attempt failed` section
   and re-invokes the subagent. Up to 2 retries.
3. If still failing, move the posting to `rejected/{{run_date}}/<id>.json`
   with the failure reason recorded.

## Pipeline per posting

```
sample-batch (once)
  │
  ▼
┌───────────────────────────────────────────────────────────────┐
│ for each posting_id:                                          │
│                                                               │
│   prepare        (Bash, deterministic)                        │
│     load raw HTML → normalize → blocks → input.json           │
│                                                               │
│   render split-in → Agent(splitter) → validate split-out      │
│                                                               │
│   for each section kind identified in split-out:              │
│     render extract-in → Agent(extract_<kind>) → validate      │
│                                                               │
│   render globals-in → Agent(extract_globals) → validate       │
│                                                               │
│   merge (Bash) → posting.json                                 │
│   validate posting.json against schemas/posting.schema.json   │
│                                                               │
│   QA (orchestrator judgment):                                 │
│     obvious null-where-populated errors? split coverage       │
│     <50% of content? → reject with reason                     │
│                                                               │
│   canonicalize (Bash) → canonical/<date>/<id>.json sidecar    │
│                                                               │
│   move to samples/ or rejected/                               │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
upload-batch (once)
  labeller upload --date {{ run_date }}  → pushes samples/ +
    canonical/ + schemas/ + README.md to HuggingFace
```

## Storage layout

### Local (gitignored under `data/postings-labelled/`)

```
data/postings-labelled/
  _runs/{{date}}/<id>/          # intermediates (input, per-task in/out, merged)
  staging/{{date}}/<id>.json    # QA-pending
  samples/{{date}}/<id>.json    # QA-accepted; uploaded to HF
  rejected/{{date}}/<id>.json   # reason recorded; stays local
  canonical/{{date}}/<id>.json  # upload-time sidecar
```

### HuggingFace dataset layout (`viktoroo/jobseek-postings-labelled`)

```
samples/{{date}}/<id>.json      # the gold — pristine, never rewritten
canonical/{{date}}/<id>.json    # sidecar, regenerable by re-running canonicalizer
schemas/posting.schema.json     # versioned
canonicalizer/{{version}}/      # rule sources for the canonicalization run
README.md
```

## Schema (summary — full JSON Schemas in `apps/crawler/src/labeller/schemas/`)

### Field buckets

| bucket | canonicalized? | language |
|---|---|---|
| **verbatim** (title, description text/html, section anchors, mission, equity description, responsibilities bullets, location.raw) | no | source |
| **free-text canonicalizable** (occupation, seniority, technologies, skills, industries, team name/function, certifications, collaboration partners, perks, city/region) | yes | English-normalized |
| **free-text non-canonicalized** (role_summary, physical_requirements) | no | English or source |
| **closed primitive** (enums, ints, bools, ISO codes) | n/a | — |

### Section kinds (closed vocab, 8)

`company` · `team` · `role` · `requirements` · `preferred` · `benefits` ·
`application` · `legal`

### Block IDs instead of anchors

The deterministic normalizer produces a list of top-level HTML blocks
(`p`, `ul`, `ol`, `li`, `h2`–`h4`, `blockquote`) numbered 0..N. Sections
are identified by contiguous block-ID ranges. Gaps are allowed.

## Canonicalization

**Inputs**: only free-text canonicalizable fields. Verbatim content and
enums are out of scope.

**Implementation (v0.1.0)**: rule-based matching via
`apps/crawler/src/labeller/canonicalize.py`. Reuses crawler CSV
taxonomies (`technologies.csv`, `occupations.csv`, `locations.csv` etc.).
RapidFuzz for fuzzy matches. Unmapped strings are recorded in the sidecar's
`unmapped[]` with a field path and reason.

**Storage**: always a sidecar at `canonical/{{date}}/<id>.json` — never
merged into the gold. Versioned via a top-level `canonicalizer_version`
field. When v0.2.0 lands, we re-run over historical gold and diff
coverage.

## Sampling

`labeller sample --date {{date}} --count N` queries the crawler's local
Postgres for postings with `first_seen_at` in the last 24h, groups by
company, samples one per company until reaching N (or exhausting
companies), then fills with a weighted tail drawing under-represented
occupations + locales. Sampler is deterministic given a seed so runs can
be replayed.

## PII and legal posture (public repo)

We store the description as it was publicly posted. No regex scrub.
Takedown-on-contact documented in `README.md` on the HF repo. This is
the same standard a public-web search index applies; our scale is tiny
by comparison.

## Operational

### Invocation

- Manual: `/jobseek-label-daily --date 2026-04-25 --count 10`
- Scheduled: via the `schedule` skill, firing the slash-command daily at
  09:00 UTC.

### Dependencies

Added to `apps/crawler/pyproject.toml`:

- `beautifulsoup4` — HTML normalization
- `jsonschema` — validation
- `rapidfuzz` — canonicalizer fuzzy match

`huggingface_hub` and `jinja2` are already present.

### Authentication

- HF push: reuses `HF_TOKEN` from `apps/crawler/.env.local` (auto-loaded by
  `labeller/cli.py` via `python-dotenv`, same pattern as
  `src/workspace/trace.py`).
- DB read for sampling: reuses `LOCAL_DATABASE_URL` from `.env.local`.
- Nothing else needed.

## Why block-IDs beat anchoring

Anchor-based (old plan):
```
{"kind": "company", "anchor_start": "Stripe builds…", "anchor_end": "…internet."}
```

Block-ID (current plan):
```
{"kind": "company", "block_ids": [0, 1, 2]}
```

| axis | anchor | block ID |
|---|---|---|
| subagent output | two 20–80 char strings | list of ints |
| failure modes | paraphrase, whitespace drift, ambiguous | invalid ID, overlap |
| validation | fuzzy text search | set operations |
| display rendering | slice text by span | concatenate block HTML |
| stability under re-normalization | anchors break | re-label only if block boundaries changed |

## Related routines

- [14 — Daily error review](14-error-review-routine.md) — sibling
  scheduled routine, error surface rather than data collection.
