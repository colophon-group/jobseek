"""Tests for `labeller scrub` — retroactive HF row removal.

We don't hit the live HF API. A ``StubHfApi`` simulates the published
dataset state by holding `data/<date>.jsonl` blobs in-memory. The scrub
flow downloads (file written to a local tempdir), filters, and either
re-uploads or deletes; the tests assert on the recorded calls and final
in-memory state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.labeller.scrub import (
    HF_REPO,
    ScrubFilter,
    ScrubGuardError,
    scrub,
)


def _row(posting_id: str, slug: str) -> dict:
    return {
        "id": posting_id,
        "labelling_meta": {"qa_verdict": "accepted"},
        "source": {"company_slug": slug},
    }


class StubHfApi:
    """In-memory HF dataset stand-in.

    `files` maps `data/<date>.jsonl` -> list of row dicts. README and any
    other files are tracked in `extras` so README refresh is observable.
    """

    def __init__(self, files: dict[str, list[dict]]):
        self.files = dict(files)
        self.extras: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []

    # -- API surface used by scrub --------------------------------------

    def list_repo_files(self, *, repo_id: str, repo_type: str) -> list[str]:
        self.calls.append(("list_repo_files", {"repo_id": repo_id, "repo_type": repo_type}))
        return sorted(list(self.files.keys()) + list(self.extras.keys()))

    def hf_hub_download(
        self,
        *,
        repo_id: str,
        filename: str,
        repo_type: str,
        local_dir: str,
    ) -> str:
        self.calls.append(
            (
                "hf_hub_download",
                {"filename": filename, "local_dir": local_dir, "repo_id": repo_id},
            )
        )
        rows = self.files[filename]
        local = Path(local_dir) / Path(filename).name
        local.parent.mkdir(parents=True, exist_ok=True)
        with local.open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return str(local)

    def upload_file(
        self,
        *,
        path_or_fileobj: str,
        path_in_repo: str,
        repo_id: str,
        repo_type: str,
        commit_message: str,
    ) -> None:
        self.calls.append(
            (
                "upload_file",
                {
                    "path_in_repo": path_in_repo,
                    "commit_message": commit_message,
                },
            )
        )
        text = Path(path_or_fileobj).read_text()
        if path_in_repo.startswith("data/") and path_in_repo.endswith(".jsonl"):
            self.files[path_in_repo] = [
                json.loads(line) for line in text.splitlines() if line.strip()
            ]
        else:
            self.extras[path_in_repo] = text

    def delete_file(
        self,
        *,
        path_in_repo: str,
        repo_id: str,
        repo_type: str,
        commit_message: str,
    ) -> None:
        self.calls.append(
            (
                "delete_file",
                {"path_in_repo": path_in_repo, "commit_message": commit_message},
            )
        )
        self.files.pop(path_in_repo, None)
        self.extras.pop(path_in_repo, None)


# --- guards --------------------------------------------------------------


def test_scrub_without_filters_refuses() -> None:
    with pytest.raises(ScrubGuardError, match="--slug or --posting-id"):
        scrub(ScrubFilter(), dry_run=True)


def test_scrub_with_only_date_refuses() -> None:
    """`--date` alone is vacuous — would wipe whole files; use delete_file."""
    with pytest.raises(ScrubGuardError, match="--slug or --posting-id"):
        scrub(ScrubFilter(dates=frozenset({"2026-04-25"})), dry_run=True)


# --- slug matching -------------------------------------------------------


def test_scrub_drops_rows_matching_slug() -> None:
    api = StubHfApi(
        {
            "data/2026-04-25.jsonl": [_row("p1", "acme"), _row("p2", "widgets")],
        }
    )
    result = scrub(ScrubFilter(slug="acme"), api=api)
    assert result.total_dropped == 1
    assert api.files["data/2026-04-25.jsonl"] == [_row("p2", "widgets")]
    # surviving rewrite call, not delete
    kinds = [c[0] for c in api.calls]
    assert "upload_file" in kinds
    assert "delete_file" not in [
        c[0]
        for c in api.calls
        if "delete" in c[0] and c[1].get("path_in_repo", "").startswith("data/")
    ]


def test_scrub_slug_match_is_case_insensitive() -> None:
    """Mirror the lowercased opt-out flow in upload._load_optout."""
    api = StubHfApi(
        {
            "data/2026-04-25.jsonl": [_row("p1", "Acme"), _row("p2", "widgets")],
        }
    )
    result = scrub(ScrubFilter(slug="ACME"), api=api)
    assert result.total_dropped == 1
    assert api.files["data/2026-04-25.jsonl"] == [_row("p2", "widgets")]


# --- multi-date iteration -----------------------------------------------


def test_scrub_iterates_all_dates_when_unfiltered_by_date() -> None:
    api = StubHfApi(
        {
            "data/2026-04-25.jsonl": [_row("p1", "acme"), _row("p2", "widgets")],
            "data/2026-04-26.jsonl": [_row("p3", "acme")],
            "data/2026-04-27.jsonl": [_row("p4", "widgets")],
        }
    )
    result = scrub(ScrubFilter(slug="acme"), api=api)
    assert result.total_dropped == 2
    # Each dated file was downloaded
    downloaded = {c[1]["filename"] for c in api.calls if c[0] == "hf_hub_download"}
    assert downloaded == {
        "data/2026-04-25.jsonl",
        "data/2026-04-26.jsonl",
        "data/2026-04-27.jsonl",
    }
    # 2026-04-26 was the last acme row; file was deleted, not re-uploaded
    assert "data/2026-04-26.jsonl" not in api.files
    # 2026-04-27 had no acme rows; left untouched (no upload_file for it)
    rewrites = {
        c[1]["path_in_repo"]
        for c in api.calls
        if c[0] == "upload_file" and c[1]["path_in_repo"].startswith("data/")
    }
    assert "data/2026-04-27.jsonl" not in rewrites


def test_scrub_date_filter_limits_files_processed() -> None:
    api = StubHfApi(
        {
            "data/2026-04-25.jsonl": [_row("p1", "acme")],
            "data/2026-04-26.jsonl": [_row("p2", "acme")],
        }
    )
    scrub(ScrubFilter(slug="acme", dates=frozenset({"2026-04-25"})), api=api)
    downloaded = {c[1]["filename"] for c in api.calls if c[0] == "hf_hub_download"}
    assert downloaded == {"data/2026-04-25.jsonl"}
    # untouched
    assert api.files["data/2026-04-26.jsonl"] == [_row("p2", "acme")]


# --- empty-file deletion -------------------------------------------------


def test_scrub_deletes_jsonl_when_all_rows_dropped() -> None:
    """If the only row in a date file is the scrubbed one, delete the file outright.

    Leaving an empty `data/<date>.jsonl` would expose an empty split via
    the dataset card's `data/*.jsonl` glob — a downstream reader would
    see a phantom date with zero rows.
    """
    api = StubHfApi({"data/2026-04-25.jsonl": [_row("p1", "acme")]})
    result = scrub(ScrubFilter(slug="acme"), api=api)
    assert result.total_dropped == 1
    assert "data/2026-04-25.jsonl" not in api.files
    deleted = [c for c in api.calls if c[0] == "delete_file"]
    assert len(deleted) == 1
    assert deleted[0][1]["path_in_repo"] == "data/2026-04-25.jsonl"


# --- posting-id filter + AND-semantics ----------------------------------


def test_scrub_posting_id_only() -> None:
    api = StubHfApi(
        {
            "data/2026-04-25.jsonl": [_row("p1", "acme"), _row("p2", "widgets")],
        }
    )
    scrub(ScrubFilter(posting_id="p2"), api=api)
    assert api.files["data/2026-04-25.jsonl"] == [_row("p1", "acme")]


def test_scrub_slug_and_posting_id_AND_semantics() -> None:
    """Both must match — neither filter alone drops a row."""
    api = StubHfApi(
        {
            "data/2026-04-25.jsonl": [
                _row("p1", "acme"),
                _row("p2", "widgets"),
                _row("p3", "acme"),
            ],
        }
    )
    scrub(ScrubFilter(slug="acme", posting_id="p3"), api=api)
    surviving_ids = [r["id"] for r in api.files["data/2026-04-25.jsonl"]]
    assert surviving_ids == ["p1", "p2"]


# --- dry-run -------------------------------------------------------------


def test_dry_run_does_not_call_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    api = StubHfApi({"data/2026-04-25.jsonl": [_row("p1", "acme")]})
    result = scrub(ScrubFilter(slug="acme"), dry_run=True, api=api)
    assert result.dry_run is True
    assert result.total_dropped == 1
    write_kinds = {c[0] for c in api.calls} & {"upload_file", "delete_file"}
    assert write_kinds == set()
    # File still on the in-memory HF
    assert api.files == {"data/2026-04-25.jsonl": [_row("p1", "acme")]}


def test_dry_run_render_describes_changes() -> None:
    api = StubHfApi(
        {
            "data/2026-04-25.jsonl": [_row("p1", "acme"), _row("p2", "widgets")],
            "data/2026-04-26.jsonl": [_row("p3", "acme")],
        }
    )
    result = scrub(ScrubFilter(slug="acme"), dry_run=True, api=api)
    text = result.render()
    assert "[dry-run]" in text
    assert "data/2026-04-25.jsonl" in text
    assert "data/2026-04-26.jsonl" in text
    assert "2 row(s) dropped" in text


# --- README refresh ------------------------------------------------------


def test_live_scrub_refreshes_readme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub re-uploads README so the counts line tracks post-scrub local truth."""
    monkeypatch.setenv("LABELLER_DATA_ROOT", str(tmp_path))
    # Local truth: one accepted posting on disk after the scrub
    p = tmp_path / "postings" / "2026-04-26" / "p2.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "id": "p2",
                "labelling_meta": {"qa_verdict": "accepted"},
                "source": {"company_slug": "widgets"},
            }
        )
    )
    api = StubHfApi({"data/2026-04-25.jsonl": [_row("p1", "acme")]})
    scrub(ScrubFilter(slug="acme"), api=api)

    readme_uploads = [
        c for c in api.calls if c[0] == "upload_file" and c[1]["path_in_repo"] == "README.md"
    ]
    assert len(readme_uploads) == 1
    assert "2026-04-26: 1" in api.extras["README.md"]


def test_no_change_skips_readme_refresh() -> None:
    """If nothing matches, don't touch README — quieter commit history."""
    api = StubHfApi({"data/2026-04-25.jsonl": [_row("p1", "widgets")]})
    scrub(ScrubFilter(slug="acme"), api=api)
    readme_uploads = [
        c for c in api.calls if c[0] == "upload_file" and c[1]["path_in_repo"] == "README.md"
    ]
    assert readme_uploads == []


# --- repo id is the labelled-postings dataset ---------------------------


def test_scrub_targets_published_dataset() -> None:
    api = StubHfApi({"data/2026-04-25.jsonl": [_row("p1", "acme")]})
    result = scrub(ScrubFilter(slug="acme"), api=api)
    assert result.repo_id == HF_REPO
