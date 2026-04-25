"""Tests for the labeller upload safety guards.

The fix here is the guard surface — `--confirm` for unscoped runs and a
zero-records preflight refusing to push an empty/misconfigured data root
to HuggingFace. We don't exercise the live HF push (no token, no
network); instead we monkeypatch ``huggingface_hub.HfApi`` and assert the
guard either raises ``UploadGuardError`` or proceeds far enough to call
the patched API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.labeller.upload import UploadGuardError, push_to_hub


def _write_posting(root: Path, date: str, posting_id: str, *, verdict: str) -> Path:
    p = root / "postings" / date / f"{posting_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "id": posting_id,
                "labelling_meta": {"qa_verdict": verdict},
                "source": {"company_slug": "acme"},
            }
        )
    )
    return p


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LABELLER_DATA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def stub_hf(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch HfApi.upload_folder to record calls instead of hitting HF."""
    calls: dict = {"upload_folder": []}

    class _Stub:
        def __init__(self, token: str | None = None):
            calls["init_token"] = token

        def upload_folder(self, **kwargs):
            calls["upload_folder"].append(kwargs)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _Stub)
    monkeypatch.setenv("HF_TOKEN", "test-token")
    return calls


# --- dry-run is always permitted -----------------------------------------


def test_dry_run_with_no_args_does_not_raise(data_root: Path) -> None:
    """Dry-run is the safe default — no token, no records, no flags needed."""
    out = push_to_hub(dry_run=True)
    assert "would upload" in out


def test_dry_run_with_empty_data_root_does_not_raise(data_root: Path) -> None:
    out = push_to_hub(run_date=None, dry_run=True)
    assert "would upload" in out


# --- unscoped run requires --confirm -------------------------------------


def test_unscoped_live_run_without_confirm_refuses(data_root: Path) -> None:
    _write_posting(data_root, "2026-04-25", "p1", verdict="accepted")
    with pytest.raises(UploadGuardError, match="--confirm"):
        push_to_hub(run_date=None, dry_run=False, confirm=False)


def test_unscoped_live_run_with_confirm_proceeds(data_root: Path, stub_hf: dict) -> None:
    _write_posting(data_root, "2026-04-25", "p1", verdict="accepted")
    push_to_hub(run_date=None, dry_run=False, confirm=True)
    assert len(stub_hf["upload_folder"]) == 1
    assert stub_hf["upload_folder"][0]["allow_patterns"][0] == "data/*.jsonl"


# --- zero-records preflight ---------------------------------------------


def test_unscoped_live_run_with_confirm_but_empty_root_refuses(
    data_root: Path,
) -> None:
    """`--confirm` does not bypass the zero-records check."""
    with pytest.raises(UploadGuardError, match="no accepted postings"):
        push_to_hub(run_date=None, dry_run=False, confirm=True)


def test_scoped_run_with_no_records_for_that_date_refuses(
    data_root: Path,
) -> None:
    _write_posting(data_root, "2026-04-24", "p1", verdict="accepted")
    with pytest.raises(UploadGuardError, match="no accepted postings"):
        push_to_hub(run_date="2026-04-25", dry_run=False, confirm=False)


def test_only_rejected_postings_are_treated_as_zero_records(
    data_root: Path,
) -> None:
    _write_posting(data_root, "2026-04-25", "p1", verdict="rejected")
    with pytest.raises(UploadGuardError, match="no accepted postings"):
        push_to_hub(run_date="2026-04-25", dry_run=False)


# --- scoped run skips --confirm requirement ------------------------------


def test_scoped_run_does_not_require_confirm(data_root: Path, stub_hf: dict) -> None:
    _write_posting(data_root, "2026-04-25", "p1", verdict="accepted")
    push_to_hub(run_date="2026-04-25", dry_run=False, confirm=False)
    assert len(stub_hf["upload_folder"]) == 1
    assert stub_hf["upload_folder"][0]["allow_patterns"][0] == "data/2026-04-25.jsonl"


# --- staging tempdir isolates the upload from leftover state -------------


def test_staging_does_not_leak_into_data_root(data_root: Path, stub_hf: dict) -> None:
    """A previously failed run must not leave stale JSONLs under the data root.

    The fix stages to a tempdir; after a successful upload the data root
    contains only the source `postings/` tree, not a `data/` or `schemas/`
    or `README.md` artifact.
    """
    _write_posting(data_root, "2026-04-25", "p1", verdict="accepted")
    push_to_hub(run_date="2026-04-25", dry_run=False)

    folder_path = stub_hf["upload_folder"][0]["folder_path"]
    assert not folder_path.startswith(str(data_root))
    assert not (data_root / "data").exists()
    assert not (data_root / "schemas").exists()
    assert not (data_root / "README.md").exists()
