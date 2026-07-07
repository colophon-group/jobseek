---
name: jobseek-label-daily
description: Run Jobseek's daily labelled-postings gold-dataset routine with Codex custom subagents and the existing labeller CLI. Use when asked to sample recent job postings, label postings for the HuggingFace dataset, run or debug the labelled-postings routine, or orchestrate the normalizer/splitter/extractor pipeline under apps/crawler.
---

# Jobseek Label Daily

## Overview

This routine samples public job postings from the last 24 hours, labels them with Codex project custom agents, validates the outputs, merges accepted records, and uploads accepted gold data to `viktoroo/jobseek-postings-labelled`.

The Python labeller code is deterministic orchestration only: database reads, Jinja task rendering, JSON Schema/custom validation, merge, QA, and HuggingFace upload. Do not add provider SDK calls or call OpenAI or Anthropic endpoints from `apps/crawler/src/labeller`. LLM judgment happens through the already-running, subscription-backed Codex session and project custom agents configured in the active harness; the repo mirrors durable subagent contracts under `.agents/labeller/`.

## Agents

Use these project custom agents:

- `jobseek-labeller-normalizer`: raw HTML task input -> `normalized.html`.
- `jobseek-labeller-splitter`: `split_sections` task input -> `split-out.json`.
- `jobseek-labeller-extractor`: `extract_all` task input -> `extract-all-out.json`.

Each agent invocation message must be exactly:

```text
INPUT: <rendered-input-path>
OUTPUT: <output-path>
```

The Jinja task prompts in `apps/crawler/src/labeller/prompts/tasks/` own task-specific rules and schema phrasing. Do not duplicate or rewrite those prompts in the orchestrator.

## Workflow

Run commands from `apps/crawler` with `uv run labeller ...`. Default `RUN_DATE` is `today`; default accepted-record target is `10` unless the user provides overrides. Start with `SAMPLE_SIZE=10`; if QA rejections leave fewer than 10 accepted local records for the date, sample additional candidates and continue until the accepted target is met or the sampling pool is exhausted.

1. Sample postings:

```bash
uv run labeller sample --date "$RUN_DATE" --count "$SAMPLE_SIZE" \
  --out "data/postings-labelled/_runs/$RUN_DATE/sample.json"
```

2. For each sampled posting ID, set `RUN_DIR=data/postings-labelled/_runs/$RUN_DATE/$POSTING_ID`.

3. Prepare raw input:

```bash
uv run labeller prepare-pre-llm "$POSTING_ID" --date "$RUN_DATE"
```

Skip the posting if the command exits non-zero.

4. Render and run normalization:

```bash
uv run labeller render-task --task normalize_html \
  --input "$RUN_DIR/raw_input.json" \
  --out "$RUN_DIR/normalize-in.md" \
  --output-path "$RUN_DIR/normalized.html"
```

Invoke `jobseek-labeller-normalizer` with the two-line `INPUT`/`OUTPUT` message. Then check that `normalized.html` exists, is non-empty, and starts with `<`.

5. Finalize the deterministic input:

```bash
uv run labeller prepare-post-llm "$POSTING_ID" --date "$RUN_DATE"
```

Skip the posting if coverage validation fails.

6. Render, run, and validate section splitting:

```bash
uv run labeller render-task --task split_sections \
  --input "$RUN_DIR/input.json" \
  --out "$RUN_DIR/split-in.md" \
  --output-path "$RUN_DIR/split-out.json"
```

Invoke `jobseek-labeller-splitter`, then validate:

```bash
uv run labeller validate --kind sections \
  --file "$RUN_DIR/split-out.json" \
  --context "$RUN_DIR/input.json"
```

On validation failure, re-render with `--previous-error "<validator output>"` and retry the same agent up to two times.

7. Render, run, and validate combined extraction:

```bash
uv run labeller render-task --task extract_all \
  --input "$RUN_DIR/input.json" \
  --sections "$RUN_DIR/split-out.json" \
  --out "$RUN_DIR/extract-all-in.md" \
  --output-path "$RUN_DIR/extract-all-out.json"
```

Invoke `jobseek-labeller-extractor`, then validate:

```bash
uv run labeller validate --kind extract_all \
  --file "$RUN_DIR/extract-all-out.json" \
  --context "$RUN_DIR/input.json"
```

Use the same two-retry protocol on validation failure.

8. Merge and validate the posting:

```bash
uv run labeller merge --posting "$POSTING_ID" --date "$RUN_DATE" \
  --verdict accepted \
  --out "data/postings-labelled/postings/$RUN_DATE/$POSTING_ID.json"

uv run labeller validate --kind posting \
  --file "data/postings-labelled/postings/$RUN_DATE/$POSTING_ID.json"

uv run labeller validate --kind qa \
  --file "data/postings-labelled/postings/$RUN_DATE/$POSTING_ID.json" \
  --report "$RUN_DIR/qa.json"
```

If merge or final validation cannot be repaired, use `labeller merge --verdict rejected --rationale "<reason>"` rather than hand-crafting a record. If QA fails after a valid merge, update only `labelling_meta.qa_verdict` and `labelling_meta.qa_rationale` from the QA report.

9. Upload accepted postings only when the user asked to run the routine, not during surface verification:

```bash
uv run labeller upload --date "$RUN_DATE"
```

Use `--dry-run` for upload verification.

## Legacy Fallback

The older per-section extractors (`extract_team`, `extract_role`, `extract_requirements`, `extract_preferred`, `extract_benefits`, `extract_globals`) remain available for compatibility. Prefer `extract_all` unless the user explicitly asks for granular retries or debugging of a specific section kind.

## Focused Verification

For changes to this orchestration surface, run:

```bash
git status --short
uv run --with 'pyyaml>=6' python /Users/Viktor/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/jobseek-label-daily
python3 - <<'PY'
import pathlib
expected = {
    ".agents/labeller/normalizer.md",
    ".agents/labeller/splitter.md",
    ".agents/labeller/extractor.md",
}
missing = [path for path in expected if not pathlib.Path(path).exists()]
assert not missing, missing
print("labeller agent contracts present")
PY
legacy_terms="$(printf '%s' 'son' 'net|op' 'us|Agent[(]|[.]Codex|Claude Code ' 'session|Anthropic ' 'API')"
rg -n "$legacy_terms" .codex .agents .claude || true
cd apps/crawler && uv run labeller --help
```

Do not run expensive uploads as verification.
