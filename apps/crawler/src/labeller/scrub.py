"""Retroactively remove rows from the published HuggingFace dataset.

Operator-facing companion to the opt-out filter in ``upload.py``. The
opt-out filter is preventive — it stops *future* runs from publishing
rows for a given company slug. ``scrub`` is the corrective half: it
walks every ``data/<date>.jsonl`` already on the HF dataset, drops rows
matching the operator's filters, and either re-uploads the trimmed file
or deletes it outright when nothing survives.

Workflow
--------

For each ``data/<date>.jsonl`` on the HF repo:

1. ``hf_hub_download`` to a tempdir.
2. Iterate JSONL lines and partition into ``keep`` vs ``drop`` using the
   filter predicate (slug + posting-id + date, AND-semantics).
3. If nothing dropped → no-op for that file.
4. If everything dropped → ``delete_file`` to remove the dated JSONL
   entirely (the dataset card's ``data_files: data/*.jsonl`` glob would
   otherwise expose an empty split for that date).
5. Otherwise → ``upload_file`` with the surviving rows.

After all files are processed, the README is regenerated to keep the
counts line in sync with the post-scrub local truth (mirroring the
existing upload code path; see issue #2701).

Safety
------

- A scrub call without ``--slug`` and without ``--posting-id`` is
  refused outright; allowing a no-filter run would silently wipe every
  row on every date. ``--date`` alone is intentionally insufficient
  because dropping a whole date is what ``HfApi.delete_file`` is for and
  doesn't need this command.
- ``--dry-run`` skips both the HF token check and any HF write call;
  it lists the files+rows that would change and returns.
- Slug matching reuses the same lowercased ``source.company_slug``
  comparison as ``upload._accepted_by_date`` so the opt-out filter and
  the scrub command stay in lockstep.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .upload import HF_REPO, _accepted_by_date, _readme_text


class ScrubGuardError(RuntimeError):
    """Raised when scrub refuses to run (no filters, etc.)."""


@dataclass
class ScrubFilter:
    """Row predicate. Empty fields mean "match anything".

    AND-semantics across set fields. ``slug`` is normalised to lowercase
    to match the opt-out file convention used in
    :func:`upload._accepted_by_date`.
    """

    slug: str | None = None
    posting_id: str | None = None
    dates: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.slug is not None:
            object.__setattr__(self, "slug", self.slug.lower())

    def matches_date_file(self, date: str) -> bool:
        return not self.dates or date in self.dates

    def matches_row(self, row: dict) -> bool:
        if self.slug is not None:
            row_slug = (row.get("source") or {}).get("company_slug")
            if not isinstance(row_slug, str) or row_slug.lower() != self.slug:
                return False
        return not (self.posting_id is not None and row.get("id") != self.posting_id)

    def is_vacuous(self) -> bool:
        """A filter with no row-level predicate would scrub every row.

        ``--date`` alone counts as vacuous; if the operator wants to
        delete an entire date file they should use
        ``HfApi().delete_file(...)`` directly — that's a single, atomic
        op and doesn't need this command's row-level machinery.
        """
        return self.slug is None and self.posting_id is None


@dataclass
class FileChange:
    date: str
    path: str  # `data/<date>.jsonl` on HF
    kept: int
    dropped: int
    drop_ids: list[str] = field(default_factory=list)
    deleted: bool = False  # True when the whole file was removed

    @property
    def changed(self) -> bool:
        return self.dropped > 0


@dataclass
class ScrubResult:
    repo_id: str
    files: list[FileChange]
    dry_run: bool

    @property
    def total_dropped(self) -> int:
        return sum(f.dropped for f in self.files)

    @property
    def changed_files(self) -> list[FileChange]:
        return [f for f in self.files if f.changed]

    def render(self) -> str:
        prefix = "[dry-run] " if self.dry_run else ""
        lines = [f"{prefix}scrub {self.repo_id}: {self.total_dropped} row(s) dropped"]
        for f in self.files:
            if not f.changed:
                continue
            verb = "delete" if f.deleted else "rewrite"
            sample = f.drop_ids[:5]
            tail = "" if len(f.drop_ids) <= 5 else f" (+{len(f.drop_ids) - 5} more)"
            lines.append(
                f"  {verb} {f.path} — kept {f.kept}, dropped {f.dropped}: {', '.join(sample)}{tail}"
            )
        if not self.changed_files:
            lines.append("  (no matching rows on HF)")
        return "\n".join(lines)


def _list_jsonl_files(api, repo_id: str) -> list[str]:
    """Return ``data/<date>.jsonl`` paths currently on the HF repo.

    Wrapped so tests can patch a single call rather than the whole
    ``HfApi`` listing surface.
    """
    paths = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    out: list[str] = []
    for p in paths:
        if p.startswith("data/") and p.endswith(".jsonl"):
            out.append(p)
    return sorted(out)


def _date_from_path(hf_path: str) -> str:
    return Path(hf_path).stem


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open() as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Practically the dataset is produced by `upload.py`,
                # which always emits valid JSON. Skip the corrupt line
                # rather than abort the whole scrub.
                continue


def _partition_rows(rows: Iterable[dict], predicate: ScrubFilter) -> tuple[list[dict], list[dict]]:
    keep: list[dict] = []
    drop: list[dict] = []
    for row in rows:
        if predicate.matches_row(row):
            drop.append(row)
        else:
            keep.append(row)
    return keep, drop


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def scrub(
    predicate: ScrubFilter,
    *,
    dry_run: bool = False,
    api=None,
) -> ScrubResult:
    """Walk every dated JSONL on HF and drop rows matching *predicate*.

    Parameters
    ----------
    predicate
        Row matcher. See :class:`ScrubFilter`.
    dry_run
        When ``True``, no HF tokens are required and no writes happen.
    api
        Optional pre-constructed ``HfApi`` (tests inject a stub). When
        ``None`` and not dry-run, a token is read from ``HF_TOKEN`` and
        an ``HfApi`` is constructed.
    """
    if predicate.is_vacuous():
        raise ScrubGuardError(
            "refusing to scrub without --slug or --posting-id "
            "(a no-filter run would wipe every row on every date)."
        )

    if not dry_run and api is None:
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError(
                "HF_TOKEN env var not set — cannot scrub HuggingFace dataset."
                " Set it in apps/crawler/.env.local."
            )
        from huggingface_hub import HfApi

        api = HfApi(token=token)

    if api is None:
        # dry_run with no api — use a token-less HfApi just to list
        # files. The HF dataset is public so this works without auth.
        from huggingface_hub import HfApi

        api = HfApi()

    hf_paths = _list_jsonl_files(api, HF_REPO)
    files: list[FileChange] = []

    label = predicate.slug or predicate.posting_id or "scrub"

    with tempfile.TemporaryDirectory(prefix="labeller-scrub-") as workdir_str:
        workdir = Path(workdir_str)
        for hf_path in hf_paths:
            date = _date_from_path(hf_path)
            if not predicate.matches_date_file(date):
                continue

            local = api.hf_hub_download(
                repo_id=HF_REPO,
                filename=hf_path,
                repo_type="dataset",
                local_dir=str(workdir / date),
            )
            local_path = Path(local)
            rows = list(_iter_jsonl(local_path))
            keep, drop = _partition_rows(rows, predicate)
            change = FileChange(
                date=date,
                path=hf_path,
                kept=len(keep),
                dropped=len(drop),
                drop_ids=[r.get("id") for r in drop if isinstance(r.get("id"), str)],
            )
            files.append(change)

            if dry_run or not change.changed:
                continue

            if not keep:
                api.delete_file(
                    path_in_repo=hf_path,
                    repo_id=HF_REPO,
                    repo_type="dataset",
                    commit_message=f"scrub:{label} (delete {hf_path})",
                )
                change.deleted = True
                continue

            rewritten = workdir / f"rewrite-{date}.jsonl"
            _write_jsonl(rewritten, keep)
            api.upload_file(
                path_or_fileobj=str(rewritten),
                path_in_repo=hf_path,
                repo_id=HF_REPO,
                repo_type="dataset",
                commit_message=f"scrub:{label} ({change.dropped} row(s) from {hf_path})",
            )

    if not dry_run and any(f.changed for f in files):
        _refresh_readme(api, label=label)

    return ScrubResult(repo_id=HF_REPO, files=files, dry_run=dry_run)


def _refresh_readme(api, *, label: str) -> None:
    """Re-upload README.md so the counts line reflects post-scrub local truth.

    Mirrors :func:`upload.push_to_hub`'s behaviour. The counts come from
    ``_accepted_by_date``, which already excludes opted-out slugs — so
    after the operator adds the scrubbed slug to ``labeller_optout.txt``
    (the documented workflow), the README counts and HF row counts stay
    consistent. If the operator runs scrub without updating the opt-out
    file, the README will overcount until the next regular upload reads
    the updated opt-out — acceptable: the row-level truth on HF is
    correct either way.
    """
    counts = {date: len(rows) for date, rows in _accepted_by_date(None).items()}
    text = _readme_text(counts)
    with tempfile.NamedTemporaryFile("w", delete=False, prefix="readme-", suffix=".md") as fh:
        fh.write(text)
        readme_path = fh.name
    try:
        api.upload_file(
            path_or_fileobj=readme_path,
            path_in_repo="README.md",
            repo_id=HF_REPO,
            repo_type="dataset",
            commit_message=f"scrub:{label} (refresh README counts)",
        )
    finally:
        Path(readme_path).unlink(missing_ok=True)
