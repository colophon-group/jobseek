"""Push accepted postings + schemas to HuggingFace as a tabular (JSONL) dataset.

Layout on HF:

    data/<date>.jsonl        one JSONL file per run date, one accepted posting per line
    schemas/**/*.schema.json versioned JSON Schemas (mirrored from src tree)
    README.md                dataset card with `configs:` frontmatter enabling
                             `datasets.load_dataset("viktoroo/jobseek-postings-labelled")`

HF repo: ``viktoroo/jobseek-postings-labelled`` (public dataset). Auth via
``HF_TOKEN`` (auto-loaded from ``apps/crawler/.env.local``).

Rejected postings stay local in ``postings/<date>/<id>.json`` for inspection
and are never uploaded.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .paths import data_root, schemas_dir

HF_REPO = "viktoroo/jobseek-postings-labelled"

_COUNTS_PLACEHOLDER = "__COUNTS_LINE__"


class UploadGuardError(RuntimeError):
    """Raised when an upload is refused by a safety guard.

    Caller (the CLI) is expected to print the message and exit non-zero.
    """


# Plain string, NOT an f-string — literal ``{`` / ``}`` in the dataset
# card (e.g. the `{kind, block_ids, extracted}` example rows) should pass
# through unescaped. The row-count line is injected by a post-render
# ``.replace()`` below.
_README_TEMPLATE = """\
---
language:
  - en
  - de
  - fr
  - it
  - es
  - nl
  - pl
  - cs
  - sv
  - 'no'
  - da
  - fi
  - pt
license: cc-by-4.0
pretty_name: Jobseek postings labelled
size_categories:
  - n<1K
source_datasets:
  - original
task_categories:
  - token-classification
  - text-classification
  - text-generation
tags:
  - job-postings
  - information-extraction
  - structured-data
  - multilingual
  - labour-market
  - career-pages
configs:
  - config_name: default
    data_files:
      - split: train
        path: "data/*.jsonl"
---

# jobseek-postings-labelled

Gold-standard **labelled job postings** sampled daily from public company
career pages. Produced by a Claude-Code-orchestrated pipeline with
specialised Sonnet subagents for section splitting and per-section
extraction. The dataset is the substrate for training an improved
structured-information extractor for [jseek.co](https://jseek.co).
__COUNTS_LINE__
- **Sourcing pipeline**: https://github.com/colophon-group/jobseek
- **Public consumer**: https://jseek.co
- **Routine spec**: `docs/15-data-sampling-routine.md` in the source repo.
- **Source schemas**: `apps/crawler/src/labeller/schemas/` in the source
  repo, mirrored under `schemas/` in this HF repo.

## Quick start

```python
from datasets import load_dataset

ds = load_dataset("viktoroo/jobseek-postings-labelled", split="train")
row = ds[0]
print(row["input"]["title_raw"])
print(row["labels"]["globals"]["profession"])
```

## Structure

```
data/<YYYY-MM-DD>.jsonl     one JSONL file per run date
schemas/posting.schema.json top-level record schema
schemas/sections.schema.json
schemas/section_extract/*.schema.json
schemas/globals.schema.json
schemas/qa.schema.json
README.md
```

Only postings with `labelling_meta.qa_verdict == "accepted"` are uploaded.
Rejected / in-progress postings stay on the collection machine and are
not published.

## Each row — top-level keys

- `id` — UUID of the posting (stable across runs)
- `schema_version` — always `1` in this release
- `sampled_at` / `labelled_at` — UTC timestamps
- `source` — company slug + name, board slug, crawler monitor, source
  URL (+ host), first-seen timestamp
- `input` — verbatim `title_raw`; raw + normalised HTML; plaintext;
  detected locale; char count; numbered `blocks` array
- `labels.sections` — list of `{kind, block_ids, extracted}` — block-ID
  ranges + per-kind structured fields
- `labels.globals` — `profession` (English), `seniority` (English
  free-text), `employment_type`, `locales_in_posting` (ISO-639-1),
  `locations` (verbatim raw + parsed city/region/country)
- `labelling_meta` — `qa_verdict`, optional `qa_rationale`, `retries`

### Section kinds (closed vocab, 7)

`company` · `team` · `role` · `requirements` · `preferred` · `benefits`
· `application`

`company` and `application` are identified by the splitter (span /
boilerplate classification) but have no structured extractor in this
release. `legal` was considered and cut — weak training signal, adds
splitter choice friction.

### Section-level extractions (Pass 2)

For the extractable kinds (`team`, `role`, `requirements`, `preferred`,
`benefits`):

- **role** — 1–2 sentence English summary; verbatim responsibility
  bullets (source language); collaboration partners; shift pattern;
  hours/week; on-call.
- **requirements** — years of experience; education level + strictness;
  degree fields; typed skills list (skill + category); required spoken
  languages (ISO-639-1); certifications; clearance; physical
  requirements; background check; driving licence.
- **preferred** — preferred skills + education + certifications.
- **benefits** — salary (min/max/currency/period/transparency);
  compensation type; equity (bool); remote policy; remote region;
  relocation; visa sponsorship; annual leave (days + unlimited bool);
  parental leave weeks; learning budget; other perks.
- **team** — team name; function tags.

### Cross-section globals (Pass 3)

English-normalised free-text `profession`; English free-text
`seniority`; ISO-639-1 `locales_in_posting`; `employment_type` enum;
`locations` list.

## Design notes

- **Block IDs, not character spans.** The normalizer emits a numbered
  list of top-level HTML blocks (`p`, `ul`, `ol`, `li`, `h2`–`h4`,
  `blockquote`); sections identify contiguous block-ID ranges.
- **Free-text canonicalisation is out of scope.** Labels are English-
  normalised free text; mapping to internal taxonomy IDs is a
  downstream consumer concern.
- **Multilingual gold, not translated gold.** Descriptions stay in the
  source language. Verbatim fields (title, description, responsibilities,
  `location.raw`) keep their original language; derived free-text
  fields (`profession`, skills, tools, perks, etc.) are English-
  normalised when a canonical English form exists.

## Licensing

- **Labels and schemas**: CC-BY 4.0 (freely reusable with attribution).
- **Descriptions**: original copyright belongs to each issuing
  employer. Captured as publicly posted on their career pages, at
  small scale and intended for non-commercial research and improvement
  of public job-search infrastructure.

## Takedown

If you are the owner of content in a posting and wish it removed, open
an issue at https://github.com/colophon-group/jobseek/issues with the
posting ID (the `id` field). We will remove the row and add the source
to an opt-out list for future runs.

## Data-quality gatekeeping

Every row passed these rules before upload:

- Splitter coverage >= 40% of blocks claimed by some section.
- `globals.profession` non-empty.
- `globals.employment_type` non-null.
- At least one extractable section with non-null `extracted`.
- If the role section is present, at least one responsibility.
- If the requirements section is present, at least one of
  `required_skills` / `education_level` / `years_experience_min`.

The full rule set is in `schemas/qa.schema.json` and evolves with the
pipeline.

## Citation

```
@misc{jobseek-postings-labelled-2026,
  title        = {jobseek-postings-labelled},
  author       = {Colophon Group},
  year         = {2026},
  url          = {https://huggingface.co/datasets/viktoroo/jobseek-postings-labelled},
  note         = {Labelled job postings for training structured-information extractors.
                  See https://jseek.co and https://github.com/colophon-group/jobseek.}
}
```
"""


def _readme_text(counts_by_date: dict[str, int] | None = None) -> str:
    if counts_by_date:
        rows = " · ".join(f"{d}: {n}" for d, n in sorted(counts_by_date.items(), reverse=True))
        counts_line = f"\nCurrent row counts by date: {rows}.\n"
    else:
        counts_line = ""
    return _README_TEMPLATE.replace(_COUNTS_PLACEHOLDER, counts_line)


def _accepted_by_date(run_date: str | None) -> dict[str, list[dict]]:
    base = data_root() / "postings"
    out: dict[str, list[dict]] = {}
    if not base.exists():
        return out
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        date = date_dir.name
        if run_date and date != run_date:
            continue
        accepted: list[dict] = []
        for path in sorted(date_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if (data.get("labelling_meta") or {}).get("qa_verdict") == "accepted":
                accepted.append(data)
        if accepted:
            out[date] = accepted
    return out


def push_to_hub(
    run_date: str | None = None,
    *,
    dry_run: bool = False,
    confirm: bool = False,
) -> str:
    """Push accepted postings as JSONL + schemas + README to HF.

    If ``run_date`` is set, limits upload to that single date's JSONL file;
    otherwise re-stages every date's JSONL from the local data root and
    uploads the lot. The README is regenerated every upload to keep the
    row-count line fresh.

    ``upload_folder`` is additive — it does not delete `data/<date>.jsonl`
    files on HF that the local run didn't stage. So a typo'd
    ``LABELLER_DATA_ROOT`` would not erase prior dates from the dataset
    directly; the visible damage is a misleading README (zero-count line)
    plus any stale JSONLs from a previous failed run that happen to still
    sit under the wrong root (see the tempdir-staging hardening below).

    Safety guards (live runs only — ``--dry-run`` skips both):

    - An unscoped run (no ``run_date``) must be acknowledged with
      ``confirm=True``. Catches the operator-typing-by-mistake case where
      the orchestrator's normal ``--date $RUN_DATE`` invocation is
      omitted.
    - Refuse if zero accepted postings were found under the data root.
      Catches a misconfigured / empty data root before it propagates as
      a zero-count refresh.
    """
    root = data_root()
    by_date = _accepted_by_date(run_date)

    if dry_run:
        return _describe_upload(root, by_date, run_date)

    if run_date is None and not confirm:
        raise UploadGuardError(
            "refusing to upload all dates without --confirm.\n"
            "  - Pass --date today (or YYYY-MM-DD) for a single date, or\n"
            "  - Pass --dry-run to preview what would be uploaded, or\n"
            "  - Pass --confirm to re-stage every local date."
        )

    if not by_date:
        scope = f"--date {run_date}" if run_date else "all dates"
        raise UploadGuardError(
            f"no accepted postings found under LABELLER_DATA_ROOT={root} "
            f"(scope: {scope}).\n"
            "  - Verify LABELLER_DATA_ROOT is set correctly.\n"
            "  - Use --dry-run to inspect what would be uploaded."
        )

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN env var not set — cannot upload to HuggingFace."
            " Set it in apps/crawler/.env.local."
        )

    # Stage the upload tree in a tempdir so a previously failed run can't
    # leak stale `data/<date>.jsonl` files into a later upload (the
    # ``data/*.jsonl`` allow_pattern would otherwise re-publish them).
    with tempfile.TemporaryDirectory(prefix="labeller-upload-") as stage_str:
        stage = Path(stage_str)
        data_dir = stage / "data"
        data_dir.mkdir(parents=True)
        for date, postings in by_date.items():
            jsonl_path = data_dir / f"{date}.jsonl"
            with jsonl_path.open("w") as fh:
                for p in postings:
                    fh.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")

        counts_by_date = {d: len(rows) for d, rows in by_date.items()}
        (stage / "README.md").write_text(_readme_text(counts_by_date))

        local_schemas = stage / "schemas"
        local_schemas.mkdir(parents=True)
        for p in schemas_dir().rglob("*.json"):
            rel = p.relative_to(schemas_dir())
            dst = local_schemas / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p.read_bytes())

        from huggingface_hub import HfApi

        api = HfApi(token=token)
        allow_patterns: list[str] = []
        if run_date:
            allow_patterns.append(f"data/{run_date}.jsonl")
        else:
            allow_patterns.append("data/*.jsonl")
        allow_patterns.extend(["schemas/**/*.json", "README.md"])

        api.upload_folder(
            folder_path=str(stage),
            repo_id=HF_REPO,
            repo_type="dataset",
            allow_patterns=allow_patterns,
            commit_message=(
                f"Add labelled postings for {run_date}" if run_date else "Refresh labelled postings"
            ),
        )
    return f"https://huggingface.co/datasets/{HF_REPO}"


def _describe_upload(root: Path, by_date: dict[str, list[dict]], run_date: str | None) -> str:
    scope = f"date {run_date}" if run_date else "all dates"
    lines = [f"[dry-run] would upload from {root} to {HF_REPO} ({scope}):"]
    for date, rows in sorted(by_date.items(), reverse=True):
        lines.append(f"  data/{date}.jsonl  — {len(rows)} accepted posting(s)")
    lines.append("  schemas/**/*.json  : copied from apps/crawler/src/labeller/schemas/")
    lines.append("  README.md          : regenerated at upload time")
    return "\n".join(lines)
