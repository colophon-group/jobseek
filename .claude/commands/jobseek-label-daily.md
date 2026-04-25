---
name: jobseek-label-daily
description: Daily labelled-postings routine — sample diverse job postings from the last 24h, label them via specialized subagents, upload to HuggingFace. Invoke with optional --date and --count. Spec in docs/15-data-sampling-routine.md.
allowed-tools:
  - Bash(cd apps/crawler)
  - Bash(labeller *)
  - Read
  - Write
  - Edit
  - Agent
---

You are the **orchestrator** for the daily labelled-postings routine. Follow
this playbook verbatim. Do not improvise the pipeline, do not read or modify
any subagent system prompts, do not make direct Anthropic API calls. Every
LLM step is an `Agent(...)` invocation; every deterministic step is a
`labeller` CLI call via Bash.

## Arguments

Parse any arguments passed to the command:

- `--date YYYY-MM-DD` (optional; default: today UTC)
- `--count N` (optional; default: 24)

Bind these to `RUN_DATE` and `SAMPLE_SIZE` for the rest of the run.

## Invariants

- Read/Write files only under `data/postings-labelled/` (relative to
  `apps/crawler`). The `_runs/{RUN_DATE}/<id>/` subtree is where per-posting
  intermediates live; the final gold record is a single file at
  `postings/{RUN_DATE}/<id>.json` whose `labelling_meta.qa_verdict` is the
  status (`accepted` or `rejected`).
- Never read any file under `.claude/agents/` or `apps/crawler/src/labeller/prompts/`.
  Subagents load their own prompts.
- Every `Agent` invocation uses `subagent_type="jobseek-labeller-<task>"` and
  `model="sonnet"`. The `prompt` argument is **exactly two lines**:

  ```
  INPUT: <path-to-rendered-input.md>
  OUTPUT: <path-to-write-output.json>
  ```

  Paths may contain spaces; the `INPUT: ` / `OUTPUT: ` prefixes are fixed.
- After every `Agent` call, run `labeller validate ...` in Bash. If it
  fails, retry the subagent up to 2 times by re-rendering the task input
  with `--previous-error "<validator output>"` and re-invoking. If still
  failing after 2 retries, record the failure reason and move to the next
  posting (the posting will ultimately be merged with `qa_verdict=rejected`).

## Step 0 — working directory

Run everything from `apps/crawler`:

```bash
cd apps/crawler
```

## Step 1 — sample

```bash
labeller sample --date $RUN_DATE --count $SAMPLE_SIZE \
    --out data/postings-labelled/_runs/$RUN_DATE/sample.json
```

Read the sample file. It has a `postings` array with posting IDs. Let `IDS`
be that list.

## Step 2 — per-posting labelling loop

For each `POSTING_ID` in `IDS`:

Let `RUN_DIR = data/postings-labelled/_runs/$RUN_DATE/$POSTING_ID`.

### 2a. Prepare (two stages around a Sonnet normalize)

**Stage A — load raw HTML**:

```bash
labeller prepare-pre-llm $POSTING_ID --date $RUN_DATE
```

Writes `$RUN_DIR/raw_input.json`. If non-zero exit (posting not found or no
description), skip this posting and continue.

**Stage B1 — render the normalize prompt**:

```bash
labeller render-task --task normalize_html \
    --input  $RUN_DIR/raw_input.json \
    --out    $RUN_DIR/normalize-in.md \
    --output-path $RUN_DIR/normalized.html
```

**Stage B2 — invoke the normalizer subagent**:

```
Agent(
  subagent_type="jobseek-labeller-normalizer",
  prompt="INPUT: $RUN_DIR/normalize-in.md\nOUTPUT: $RUN_DIR/normalized.html",
  model="sonnet"
)
```

The subagent produces `$RUN_DIR/normalized.html` — a clean HTML subset
with text content preserved verbatim. No JSON validation (output is HTML,
not JSON); check only that the file is non-empty and starts with `<`.

**Stage C — finalize input.json**:

```bash
labeller prepare-post-llm $POSTING_ID --date $RUN_DATE
```

Reads `raw_input.json` + `normalized.html`, runs a deterministic
tail-pass (attribute-strip, disallowed-tag-unwrap, text-coverage check),
extracts blocks, writes `$RUN_DIR/input.json`. If the coverage ratio is
below 0.7 (i.e. the normalizer silently dropped too much content), this
exits non-zero — skip the posting and continue.

### 2b. Split sections

```bash
labeller render-task --task split_sections \
    --input  $RUN_DIR/input.json \
    --out    $RUN_DIR/split-in.md \
    --output-path $RUN_DIR/split-out.json
```

Invoke the splitter:

```
Agent(
  subagent_type="jobseek-labeller-splitter",
  prompt="INPUT: $RUN_DIR/split-in.md\nOUTPUT: $RUN_DIR/split-out.json",
  model="sonnet"
)
```

Validate:

```bash
labeller validate --kind sections \
    --file    $RUN_DIR/split-out.json \
    --context $RUN_DIR/input.json
```

On failure, retry up to 2 times by re-rendering with
`--previous-error "<validator stderr>"` and re-invoking.

### 2c. Combined per-section extraction + globals (preferred path)

One Sonnet call produces per-section `extracted` fields for every
extractable kind (`team`, `role`, `requirements`, `preferred`,
`benefits`) AND the cross-section `globals` block. `company` /
`application` sections are reproduced with `extracted: null` (span
classification only).

```bash
labeller render-task --task extract_all \
    --input    $RUN_DIR/input.json \
    --sections $RUN_DIR/split-out.json \
    --out      $RUN_DIR/extract-all-in.md \
    --output-path $RUN_DIR/extract-all-out.json
```

```
Agent(
  subagent_type="jobseek-labeller-extractor",
  prompt="INPUT: $RUN_DIR/extract-all-in.md\nOUTPUT: $RUN_DIR/extract-all-out.json",
  model="sonnet"
)
```

Validate:

```bash
labeller validate --kind extract_all \
    --file $RUN_DIR/extract-all-out.json \
    --context $RUN_DIR/input.json
```

The validator schema-checks the outer document, then validates each
`sections[].extracted` against its per-kind schema and the `globals`
block against `globals.schema.json`. Block-ID contiguity / overlap /
existence are re-checked against the splitter's block list.

Retry policy same as 2b.

#### Granular fallback (rollback path)

If you need finer retry granularity (per-section retries), the legacy
path is still available:

```bash
# Run 5 per-section extractors in parallel, then one globals call.
# merge.py falls back to this layout when extract-all-out.json is absent.
```

See the earlier revision of this command file for the full per-section
commands.

### 2e. Merge

```bash
labeller merge --posting $POSTING_ID --date $RUN_DATE \
    --verdict accepted \
    --out data/postings-labelled/postings/$RUN_DATE/$POSTING_ID.json
```

If `merge` fails (missing extract files from an irrecoverable retry loop),
write a minimal rejected record and move on — e.g. via a fallback merge
with `--verdict rejected --rationale "merge failed: <reason>"`. Do NOT
hand-craft the JSON.

### 2f. Validate merged (schema)

```bash
labeller validate --kind posting \
    --file data/postings-labelled/postings/$RUN_DATE/$POSTING_ID.json
```

If schema validation fails, edit the file to set
`labelling_meta.qa_verdict = "rejected"` and
`labelling_meta.qa_rationale = "posting schema failed: <validator output>"`.

### 2g. QA validation (concrete rules, not subjective)

```bash
labeller validate --kind qa \
    --file data/postings-labelled/postings/$RUN_DATE/$POSTING_ID.json \
    --report $RUN_DIR/qa.json
```

The `qa` validator runs rules including split-coverage ≥ 40%, non-null
`globals.profession`, non-null `globals.employment_type`, ≥ 1
extractable section, each section has non-null `extracted`,
role→non-empty responsibilities, requirements→at least one signal.

If validation fails (non-zero exit), update the posting file's
`labelling_meta`:

- `qa_verdict = "rejected"`
- `qa_rationale = "<the rule name(s) that failed>"`

Read `$RUN_DIR/qa.json` for the full rule breakdown to populate the
rationale cleanly.

### 2h. (no step — canonicalizer removed)

## Step 3 — upload

After the loop, upload the accepted postings:

```bash
labeller upload --date $RUN_DATE
```

Only postings with `labelling_meta.qa_verdict == "accepted"` are sent to
HuggingFace; rejected ones stay local for inspection. `labeller upload`
reads `HF_TOKEN` from `apps/crawler/.env.local` automatically.

`upload` refuses an unscoped run (no `--date`) without `--confirm`, and
refuses any live run when zero accepted postings are present (catches a
typo'd `LABELLER_DATA_ROOT`). Use `--dry-run` to preview at any time.

## Step 4 — summary

Print a final summary:

- Sampled: N
- Accepted: A
- Rejected: R (with the first-failing qa rule per posting)
- HF upload URL (or "skipped — no HF_TOKEN")

## Hard rules

- Never `rm -rf`. Rejected postings stay on disk (in the same `postings/`
  directory, distinguished by `qa_verdict`).
- Never modify the orchestrator's own prompt file or subagent files.
- Never make direct Anthropic API calls — everything is an `Agent(...)` tool
  call inside this Claude Code session.
- Never invoke `gh`, `git`, `curl`, `mkdir`, `rm`, `mv`, `cat`,
  `python`, or any other shell command outside `labeller *`
  during a labelling run. The pipeline only needs `labeller` CLI, `Read`,
  `Write`, `Edit`, and `Agent`. The frontmatter pre-approves exactly
  that surface; anything else falls through to the user's default
  permission policy and will surface a prompt — treat that prompt as a
  stop signal, not as a request to approve.
- If any command fails unexpectedly (non-zero exit that isn't a known
  validate-failed path), stop the run for that posting, record the error,
  continue to the next posting.
- If more than 50% of postings end with `qa_verdict=rejected`, stop after
  the current posting and emit a warning in the summary — the normalizer
  or a subagent may need attention.

## Security model

The `allowed-tools:` frontmatter at the top of this file is **additive**:
it pre-approves the legitimate orchestrator surface (`labeller` Bash
invocations, `Read`, `Write`, `Edit`, `Agent`) so the routine runs
without per-step prompts. It does **not** deny anything — Claude Code
permissions are augmentative, not restrictive. The actual containment
surface is:

1. The Hard rules above, which forbid out-of-lane invocations.
2. The user's default permission policy: any tool call outside the
   pre-approved set surfaces a permission prompt that the operator can
   reject. A prompt during a labelling run is itself a red flag — the
   playbook should never need approval beyond what's pre-approved.
3. For stronger enforcement, the operator can add `Bash(gh *)`,
   `Bash(git *)`, `Bash(curl *)` to the `deny` list in
   `.claude/settings.json` (project-wide) or
   `.claude/settings.local.json` (operator-local). Project-wide deny is
   currently *not* set because it would interfere with non-labeller
   sessions that legitimately need those tools.
