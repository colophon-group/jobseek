"""Push the local gold + canonical sidecars + schemas to the HuggingFace dataset.

HF repo: ``viktoroo/jobseek-postings-labelled`` (dataset type, public).
Auth: ``HF_TOKEN`` env var (same token as the agent-traces upload).

This is a one-shot script — safe to re-run. Re-uploads are deduped by HF
based on content.
"""

from __future__ import annotations

import os
from pathlib import Path

from .paths import canonical_dir, data_root, samples_dir, schemas_dir

HF_REPO = "viktoroo/jobseek-postings-labelled"


def _readme_text() -> str:
    return """# jobseek-postings-labelled

Labelled job postings sampled daily from public career pages, used to train
an improved structured-information extractor for [jseek.co](https://jseek.co).

## Contents

- `samples/YYYY-MM-DD/<posting_id>.json` — gold labels. Free-text fields are
  English-normalized; verbatim content (title, description, section text,
  mission, responsibilities bullets) is preserved in the source language.
- `canonical/YYYY-MM-DD/<posting_id>.json` — rule-based mapping of free-text
  labels to internal taxonomy IDs. Sidecar artifact; regenerable.
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


def push_to_hub(run_date: str | None = None, *, dry_run: bool = False) -> str:
    """Push local ``samples/`` + ``canonical/`` + schemas + README to HF.

    If ``run_date`` is set, limits upload to that single date's folder;
    otherwise uploads the whole ``samples/`` + ``canonical/`` tree.
    """
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN env var not set — cannot upload to HuggingFace."
            " Set it in apps/crawler/.env.local."
        )

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    root = data_root()

    # Ensure README exists locally (overwritten every run to stay in sync)
    readme_path = root / "README.md"
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text(_readme_text())

    allow_patterns = []
    if run_date:
        samples = samples_dir(run_date)
        canonical = canonical_dir(run_date)
        if samples.exists():
            allow_patterns.append(f"samples/{run_date}/*.json")
        if canonical.exists():
            allow_patterns.append(f"canonical/{run_date}/*.json")
    else:
        allow_patterns.extend(["samples/**/*.json", "canonical/**/*.json"])
    allow_patterns.extend(["schemas/**/*.json", "README.md"])

    # Copy the canonical JSON Schemas into the data root so the upload
    # picks them up with the same folder convention.
    local_schemas = root / "schemas"
    local_schemas.mkdir(parents=True, exist_ok=True)
    src_schemas = schemas_dir()
    for p in src_schemas.rglob("*.json"):
        rel = p.relative_to(src_schemas)
        dst = local_schemas / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(p.read_bytes())

    if dry_run:
        return _describe_upload(root, allow_patterns)

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


def _describe_upload(root: Path, patterns: list[str]) -> str:
    lines = [f"[dry-run] would upload from {root} to {HF_REPO}:"]
    for pat in patterns:
        matches = list(root.glob(pat))
        lines.append(f"  {pat} -> {len(matches)} file(s)")
    return "\n".join(lines)
