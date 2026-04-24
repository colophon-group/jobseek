---
name: jobseek-label-daily
description: Daily labelled-postings routine — sample diverse job postings from the last 24h, label them via specialized subagents, upload to HuggingFace. Invoke with optional --date and --count. Spec in docs/15-data-sampling-routine.md.
---

You are the **orchestrator** for the daily labelled-postings routine. Follow
this playbook verbatim. Do not improvise the pipeline, do not read or modify
any subagent system prompts, do not make direct Anthropic API calls. Every
LLM step is an `Agent(...)` invocation; every deterministic step is a
`labeller` CLI call via Bash.

## Arguments

Parse any arguments passed to the command:

- `--date YYYY-MM-DD` (optional; default: today UTC)
- `--count N` (optional; default: 10)

Bind these to `RUN_DATE` and `SAMPLE_SIZE` for the rest of the run.

## Invariants

- Read/Write files only under `data/postings-labelled/` (relative to
  `apps/crawler`). The `_runs/{RUN_DATE}/<id>/` subtree is where per-posting
  intermediates live.
- Never read any file under `.claude/agents/` or `apps/crawler/src/labeller/prompts/`.
  Subagents load their own prompts.
- Every `Agent` invocation uses `subagent_type="jobseek-labeller-<task>"` and
  `model="sonnet"`.
- After every `Agent` call, run `labeller validate ...` in Bash. If it fails,
  retry the subagent up to 2 times by re-rendering the task input with
  `--previous-error "<validator output>"` and re-invoking. If still failing
  after 2 retries, move the posting to `rejected/{RUN_DATE}/<id>.json` with
  the failure reason and continue to the next posting.

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

### 2a. Prepare

```bash
labeller prepare $POSTING_ID --date $RUN_DATE
```

This writes `data/postings-labelled/_runs/$RUN_DATE/$POSTING_ID/input.json`.
If it exits non-zero (posting not found or normalizer coverage too low),
skip this posting; log the reason and continue.

Let `RUN_DIR = data/postings-labelled/_runs/$RUN_DATE/$POSTING_ID`.

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
  prompt="input=$RUN_DIR/split-in.md output=$RUN_DIR/split-out.json",
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
`--previous-error "<validator stderr>"` and re-invoking the same Agent. If
still failing, reject and continue.

### 2c. Per-section extraction

Read `$RUN_DIR/split-out.json`. For each unique kind in its `sections[].kind`
array that is **not** `legal` (legal has no structured extraction):

```bash
labeller render-task --task extract_$KIND \
    --input    $RUN_DIR/input.json \
    --sections $RUN_DIR/split-out.json \
    --kind     $KIND \
    --out      $RUN_DIR/extract-$KIND-in.md \
    --output-path $RUN_DIR/extract-$KIND-out.json
```

```
Agent(
  subagent_type="jobseek-labeller-extract-$KIND",
  prompt="input=$RUN_DIR/extract-$KIND-in.md output=$RUN_DIR/extract-$KIND-out.json",
  model="sonnet"
)
```

Validate:

```bash
labeller validate --kind $KIND --file $RUN_DIR/extract-$KIND-out.json
```

Retry policy same as 2b.

### 2d. Globals

```bash
labeller render-task --task extract_globals \
    --input    $RUN_DIR/input.json \
    --sections $RUN_DIR/split-out.json \
    --out      $RUN_DIR/globals-in.md \
    --output-path $RUN_DIR/globals-out.json
```

```
Agent(
  subagent_type="jobseek-labeller-extract-globals",
  prompt="input=$RUN_DIR/globals-in.md output=$RUN_DIR/globals-out.json",
  model="sonnet"
)
```

```bash
labeller validate --kind globals --file $RUN_DIR/globals-out.json
```

Retry policy same as 2b.

### 2e. Merge

```bash
labeller merge --posting $POSTING_ID --date $RUN_DATE \
    --verdict accepted \
    --out data/postings-labelled/staging/$RUN_DATE/$POSTING_ID.json
```

### 2f. Validate merged

```bash
labeller validate --kind posting \
    --file data/postings-labelled/staging/$RUN_DATE/$POSTING_ID.json
```

If invalid, move to `rejected/$RUN_DATE/$POSTING_ID.json` with the
validator output as the rejection reason. (Read staging, append
`labelling_meta.qa_verdict = "rejected"` and `qa_rationale = "<reason>"`,
write to rejected/, delete from staging.)

### 2g. QA judgment

Read the staging file. Check:

- Is there at least one block in the description that the splitter missed
  which is obviously content (a paragraph > 80 chars that's not a heading)?
- Are all required-skills empty when the description text clearly contains
  "experience with X" / "proficient in Y" phrases?
- Is `labels.globals.occupation` obviously wrong given the title?

If any of these fail, move to `rejected/` with a short rationale. Otherwise
move to `samples/$RUN_DATE/$POSTING_ID.json` (use `mv`).

### 2h. Canonicalize

Only for accepted samples:

```bash
labeller canonicalize \
    --file data/postings-labelled/samples/$RUN_DATE/$POSTING_ID.json \
    --out  data/postings-labelled/canonical/$RUN_DATE/$POSTING_ID.json
```

Note the coverage line printed; add to the run summary.

## Step 3 — upload

After the loop:

```bash
labeller upload --date $RUN_DATE
```

`labeller upload` reads `HF_TOKEN` from `apps/crawler/.env.local` automatically.
If the token is missing there, the step fails cleanly — report that gold is in
`samples/` locally and the upload can be re-run after setting the token.

## Step 4 — summary

Print a final summary:

- Sampled: N
- Accepted: A
- Rejected: R (by reason)
- Average canonicalizer coverage: X%
- HF upload URL (or "skipped — no HF_TOKEN")

## Hard rules

- Never `rm -rf`. Rejected files stay on disk for inspection.
- Never modify the orchestrator's own prompt file or subagent files.
- Never make direct Anthropic API calls — everything is an `Agent(...)` tool
  call inside this Claude Code session.
- If any command fails unexpectedly (non-zero exit that isn't the known
  validate-failed path), stop the run for that posting, record the error,
  continue to the next posting.
- If more than 50% of postings end in `rejected/`, stop after the current
  posting and emit a warning in the summary — the normalizer or a subagent
  may need attention.
