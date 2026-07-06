# Daily labelled-postings routine

Scheduled agent routine that samples job postings, labels them via
task-specific subagents, and uploads a gold dataset to a public HuggingFace
dataset repo (`viktoroo/jobseek-postings-labelled`). Codex is the preferred
path for new pilots; the existing Claude Code slash command remains a
compatible alternate path. The dataset is the substrate for training a better
structured-information extractor to replace the current crawler heuristics.

## Why an agent session and not direct provider APIs

The routine runs inside an interactive or noninteractive coding-agent session.
The orchestrator is the session itself; subagents are invoked through the
agent runtime, not by direct Anthropic/OpenAI API calls from repository code.
That keeps the collected training data separate from the future production
model that will be trained on this dataset.

## Architecture

```
Agent session (Codex preferred, Claude Code compatible)
  ├─ Reads the repo skill or slash command playbook
  ├─ Shell-calls `labeller <subcommand>` for deterministic steps
  │    ├─ sample       — diverse per-company selection from the last 24h
  │    ├─ prepare      — load raw HTML, normalize, compute blocks
  │    ├─ render-task  — Jinja-render a task-input markdown file
  │    ├─ validate     — JSON-schema + custom rules + qa gatekeeper
  │    ├─ merge        — assemble per-subagent outputs into one posting.json
  │    └─ upload       — push accepted postings to HF
  │
  └─ subagent invocations for every LLM step
       ├─ jobseek-labeller-normalizer          Pass 0: clean HTML
       ├─ jobseek-labeller-splitter            Pass 1: sections
       └─ jobseek-labeller-extractor           Pass 2: extract_all
```

The preferred Codex path uses **3 subagent calls per posting**: normalize,
split, and combined extraction. The legacy granular path with five
per-section extractors plus globals remains available for rollback or focused
debugging, but `extract_all` is the default because it reduces orchestration
surface and retry coordination. The `company` and `application` section kinds
are identified by the splitter for span classification and are preserved in
the combined output with `extracted: null`.

### The "orchestrator never reads the subagent prompt" invariant

Subagent system prompts live in the agent-runtime layer. Codex repo skills
belong under `.agents/skills`; the legacy Claude-compatible prompts live in
`.claude/agents/jobseek-labeller-*.md`. The orchestrator's context must never
contain those full subagent prompts. It passes variables to a subagent by
handing it a **rendered task-input file path**. The subagent prompt body is
always two lines:

```
INPUT: <path-to-rendered-input.md>
OUTPUT: <path-to-write-output.json>
```

- Jinja template (`prompts/tasks/<task>.md.j2`) owns variable-insertion and phrasing.
- Subagent definition owns the task contract and output discipline.
- Orchestrator owns only the workflow and paths.

### Retry protocol

After every subagent invocation:

1. `labeller validate --kind <kind> --file <output_path>` runs schema + custom checks (block-ID coverage, overlap, enum membership, etc.).
2. If it fails, the orchestrator renders a new input file with the validator's error appended to a `## Previous attempt failed` section and re-invokes the subagent. Up to 2 retries.
3. If still failing, the posting is merged with `qa_verdict=rejected` and stays in `postings/<date>/<id>.json` for inspection. Rejected postings are **never uploaded to HF**.

## Pipeline per posting

```
sample-batch (once)
  │
  ▼
┌───────────────────────────────────────────────────────────────┐
│ for each posting_id:                                          │
│                                                               │
│   prepare-pre-llm  (Bash)                                     │
│     load raw HTML → raw_input.json                            │
│                                                               │
│   render normalize-in → Agent(normalizer) → normalized.html   │
│                                                               │
│   prepare-post-llm (Bash, deterministic)                      │
│     normalized HTML → blocks → input.json                     │
│                                                               │
│   render split-in → Agent(splitter) → validate split-out      │
│                                                               │
│   render extract-all-in → Agent(extractor)                    │
│     → validate extract-all-out                                │
│                                                               │
│   merge (Bash) → postings/<date>/<id>.json                    │
│   validate --kind posting  (schema)                           │
│   validate --kind qa       (concrete rule gatekeeper)         │
│                                                               │
│   on qa failure: set labelling_meta.qa_verdict = "rejected"   │
│                  + qa_rationale                                │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
upload-batch (once)
  labeller upload --date {{ run_date }}  → pushes accepted postings
                                           + schemas + README to HF
```

## QA rules (concrete, not subjective)

`labeller validate --kind qa --file <posting.json>` runs heuristic rules
on a merged posting and emits a structured report matching
`schemas/qa.schema.json`. Rules are a gatekeeper for training-signal
quality, not a judge:

- `split_coverage_min_40pct` — the splitter's sections cover ≥40% of blocks.
- `profession_non_empty` — `globals.profession` is non-null and non-empty.
- `employment_type_non_null` — `globals.employment_type` is set.
- `has_extractable_section` — at least one of team/role/requirements/preferred/benefits present.
- `section_<kind>_has_extraction` — every extractable section has non-null `extracted`.
- `role_responsibilities_non_empty` — when the role section is present, it has ≥1 responsibility.
- `requirements_has_signal` — when requirements are present, at least one of `required_skills` / `education_level` / `years_experience_min`.

A posting is accepted iff every rule passes. Rejected postings are kept
locally for inspection and analysis but never uploaded.

## Storage layout

### Local (gitignored under `data/postings-labelled/`)

```
data/postings-labelled/
  _runs/{{date}}/<id>/          # intermediates (input, per-task in/out, merged)
  postings/{{date}}/<id>.json   # final gold; status is `labelling_meta.qa_verdict`
  schemas/                       # staged copies uploaded to HF
  README.md
```

Intermediates under `_runs/` are kept for debugging but never uploaded.
The gold record at `postings/<date>/<id>.json` carries its verdict in
`labelling_meta.qa_verdict`; `upload` filters for `accepted` before
pushing.

### HuggingFace dataset layout (`viktoroo/jobseek-postings-labelled`)

```
postings/{{date}}/<id>.json     # accepted gold records only
schemas/posting.schema.json     # versioned
schemas/*/*.schema.json
README.md
```

## Schema (summary — full JSON Schemas in `apps/crawler/src/labeller/schemas/`)

### Field buckets

| bucket | canonicalized? | language |
|---|---|---|
| **verbatim** (title, description text/html, section text, responsibilities bullets, location.raw) | no | source |
| **free-text** (profession, seniority, technologies, skills, industries, team name/function, certifications, collaboration partners, perks, city/region) | deferred to downstream training — no canonicalizer in this pipeline | English-normalized |
| **free-text non-canonicalized** (role_summary, physical_requirements) | no | English or source |
| **closed primitive** (enums, ints, bools, ISO codes) | n/a | — |

### Section kinds (closed vocab, 7)

`company` · `team` · `role` · `requirements` · `preferred` · `benefits` · `application`

- `company` and `application` are identified but not extracted — useful for training a boilerplate classifier.
- `legal` was considered and cut — no structured extraction, marginal training signal, adds splitter choice friction.

### Block IDs instead of anchors

The deterministic normalizer produces a list of top-level HTML blocks (`p`, `ul`, `ol`, `li`, `h2`–`h4`, `blockquote`) numbered 0..N. Sections are identified by contiguous block-ID ranges. Gaps are allowed.

## Canonicalization (out of scope for this pipeline)

Free-text labels (`profession`, `seniority`, skills, technologies, industries, etc.) are stored **as-is**. No rule-based canonicalizer runs at upload time.

Rationale: the dataset's purpose is to *train* a canonicalizer that is better than the current rule-based approach. Running the same rule-based mapping we're trying to replace — as a sidecar — would couple the dataset to the thing it's supposed to make obsolete. Canonical ID resolution is the consumer's concern (training data preprocessing, serving-side mapping, etc.), not this pipeline's.

## Sampling

`labeller sample --date {{date}} --count N` queries the crawler's local Postgres for postings with `first_seen_at` in the last 24h, groups by company, samples one per company until reaching N (or exhausting companies), then fills with a weighted tail drawing under-represented postings. Sampler is deterministic given a seed so runs can be replayed.

## PII and legal posture (public repo)

We store the description as it was publicly posted. No regex scrub. Takedown-on-contact documented in `README.md` on the HF repo. This is the same standard a public-web search index applies; our scale is tiny by comparison.

## Operational

### Invocation

- Manual Codex pilot: invoke the repo skill when present, or run
  `codex exec --json` with this runbook and the desired arguments
  (`--date 2026-04-25 --count 10`).
- Manual Claude-compatible path: `/jobseek-label-daily` (defaults: today UTC,
  24 postings).
- Scheduled Codex path: create a Codex app automation for the repo skill and
  run it on a background worktree.
- Scheduled Claude-compatible path: point the schedule at the prompt
  `/jobseek-label-daily`. The slash command file remains a compatible source
  of truth until the Codex skill is checked in and validated.

### Dependencies

Added to `apps/crawler/pyproject.toml`:

- `beautifulsoup4` — HTML normalization
- `jsonschema` — validation

`huggingface_hub` and `jinja2` are already present.

### Authentication

- HF push: reuses `HF_TOKEN` from `apps/crawler/.env.local` when running the
  labeller CLI locally; `labeller/cli.py` loads `.env.local` via
  `python-dotenv`.
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
  Codex-first scheduled routine; primary runbook is
  [`.agents/skills/jobseek-error-review/SKILL.md`](../.agents/skills/jobseek-error-review/SKILL.md).
