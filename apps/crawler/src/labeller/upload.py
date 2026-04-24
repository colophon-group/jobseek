"""Push the local gold + schemas to the HuggingFace dataset.

HF repo: ``viktoroo/jobseek-postings-labelled`` (dataset type, public).
Auth: ``HF_TOKEN`` env var (auto-loaded from ``apps/crawler/.env.local`` by
``labeller/cli.py`` via python-dotenv, same pattern as the agent-traces
upload in ``src/workspace/trace.py``).

Uploads only postings with ``labelling_meta.qa_verdict == "accepted"``.
Rejected postings stay local for inspection and do not leave the machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import data_root, postings_dir, schemas_dir

HF_REPO = "viktoroo/jobseek-postings-labelled"


def _readme_text() -> str:
    return """# jobseek-postings-labelled

Labelled job postings sampled daily from public career pages, used to train
an improved structured-information extractor for [jseek.co](https://jseek.co).

## Contents

- `postings/YYYY-MM-DD/<posting_id>.json` — gold labels. Free-text fields
  are English-normalized; verbatim content (title, description, section
  text, responsibilities bullets) is preserved in the source language.
- `schemas/posting.schema.json` — JSON Schema for a sample record.

## Source posture

Descriptions are captured as they were publicly posted on each company's
career page. We do not scrape behind authentication walls or paywalls.
We do not republish content outside what was already public at the
source URL.

## Takedown

If you are the owner of content in a posting and wish it removed, please
open an issue at https://github.com/colophon-group/jobseek/issues with
the posting ID and we will remove it and add the source to an opt-out
list.

## Licence

Labels and schema: CC0 (freely reusable).
Descriptions: original copyright belongs to the issuing employer; we
redistribute under fair-use / public-benefit terms consistent with how
search indices handle public job postings.
"""


def _accepted_only(root: Path, run_date: str | None) -> list[Path]:
    """Return the list of posting files with ``qa_verdict == "accepted"``.

    If ``run_date`` is set, limits the scan to that single date.
    """
    base = postings_dir(run_date) if run_date else root / "postings"
    out: list[Path] = []
    if not base.exists():
        return out
    for path in base.rglob("*.json"):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if (data.get("labelling_meta") or {}).get("qa_verdict") == "accepted":
            out.append(path)
    return out


def push_to_hub(run_date: str | None = None, *, dry_run: bool = False) -> str:
    """Push local ``postings/`` (accepted only) + schemas to HF.

    ``run_date`` limits the upload to that date's folder; otherwise uploads
    every accepted posting across all dates.
    """
    root = data_root()
    accepted = _accepted_only(root, run_date)

    if dry_run:
        return _describe_upload(root, accepted, run_date)

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN env var not set — cannot upload to HuggingFace."
            " Set it in apps/crawler/.env.local."
        )

    # Prepare local tree for upload (non-dry run side-effects confined here).
    readme_path = root / "README.md"
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text(_readme_text())

    local_schemas = root / "schemas"
    local_schemas.mkdir(parents=True, exist_ok=True)
    for p in schemas_dir().rglob("*.json"):
        rel = p.relative_to(schemas_dir())
        dst = local_schemas / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(p.read_bytes())

    from huggingface_hub import HfApi

    api = HfApi(token=token)

    allow_patterns: list[str] = []
    if run_date:
        allow_patterns.append(f"postings/{run_date}/*.json")
    else:
        allow_patterns.append("postings/**/*.json")
    allow_patterns.extend(["schemas/**/*.json", "README.md"])

    api.upload_folder(
        folder_path=str(root),
        repo_id=HF_REPO,
        repo_type="dataset",
        allow_patterns=allow_patterns,
        commit_message=(
            f"Add labelled postings for {run_date}" if run_date else "Refresh labelled postings"
        ),
    )
    return f"https://huggingface.co/datasets/{HF_REPO}"


def _describe_upload(root: Path, accepted: list[Path], run_date: str | None) -> str:
    scope = f"date {run_date}" if run_date else "all dates"
    lines = [f"[dry-run] would upload from {root} to {HF_REPO} ({scope}):"]
    lines.append(f"  postings (accepted): {len(accepted)} file(s)")
    lines.append("  schemas/**/*.json  : copied from apps/crawler/src/labeller/schemas/")
    lines.append("  README.md          : regenerated at upload time")
    return "\n".join(lines)
