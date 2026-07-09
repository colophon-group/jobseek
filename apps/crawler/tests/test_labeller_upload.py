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
    """Patch HfApi.upload_folder to record calls instead of hitting HF.

    Snapshots the staged folder's contents (README.md, data/*) before
    returning, since the real ``push_to_hub`` deletes the tempdir on exit
    and tests that assert on README contents need a stable handle.
    """
    calls: dict = {"upload_folder": [], "remote_files": {}}

    class _Stub:
        def __init__(self, token: str | None = None):
            calls["init_token"] = token

        def list_repo_files(self, **kwargs):
            calls.setdefault("list_repo_files", []).append(kwargs)
            return sorted(calls["remote_files"])

        def hf_hub_download(self, **kwargs):
            calls.setdefault("hf_hub_download", []).append(kwargs)
            filename = kwargs["filename"]
            local_dir = Path(kwargs["local_dir"])
            target = local_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(calls["remote_files"][filename])
            return str(target)

        def upload_folder(self, **kwargs):
            folder = Path(kwargs["folder_path"])
            snapshot: dict[str, str] = {}
            if folder.exists():
                for p in folder.rglob("*"):
                    if p.is_file():
                        rel = str(p.relative_to(folder))
                        try:
                            snapshot[rel] = p.read_text()
                        except UnicodeDecodeError:
                            snapshot[rel] = ""
            kwargs["_snapshot"] = snapshot
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


def test_upload_uses_huggingface_cache_token_when_env_token_absent(
    data_root: Path, stub_hf: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub.utils

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(huggingface_hub.utils, "get_token", lambda: "cached-token")
    _write_posting(data_root, "2026-04-25", "p1", verdict="accepted")

    push_to_hub(run_date="2026-04-25", dry_run=False)

    assert stub_hf["init_token"] == "cached-token"


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


def test_pre_existing_stale_data_dir_is_not_uploaded(data_root: Path, stub_hf: dict) -> None:
    """Stale `data/<date>.jsonl` left by a pre-this-PR run must not be re-published.

    Operators upgrading to this version may have a previous run's
    `data/`, `schemas/`, and `README.md` sitting under `LABELLER_DATA_ROOT`
    from before the tempdir-staging change. Since `upload_folder` now uses
    a tempdir as `folder_path`, those stale artifacts must not appear in
    the upload set.
    """
    stale_data = data_root / "data"
    stale_data.mkdir()
    (stale_data / "2020-01-01.jsonl").write_text('{"id":"stale"}\n')
    (data_root / "README.md").write_text("# stale\n")
    stale_schemas = data_root / "schemas"
    stale_schemas.mkdir()
    (stale_schemas / "stale.json").write_text("{}")

    _write_posting(data_root, "2026-04-25", "p1", verdict="accepted")
    push_to_hub(run_date="2026-04-25", dry_run=False)

    folder_path = Path(stub_hf["upload_folder"][0]["folder_path"])
    # The staged folder is a fresh tempdir, not data_root
    assert folder_path != data_root
    assert not str(folder_path).startswith(str(data_root))
    # The staged folder contains only what this run produced — no stale 2020-01-01
    staged_data = folder_path / "data"
    if staged_data.exists():
        assert not (staged_data / "2020-01-01.jsonl").exists()
        assert (staged_data / "2026-04-25.jsonl").exists()


def test_unscoped_run_with_empty_string_date_still_requires_confirm(
    data_root: Path,
) -> None:
    """An empty-string `--date` must not bypass the unscoped-run guard.

    `argparse` with `default=None` normally rules this out, but a caller
    that passes `run_date=""` (e.g. shell expansion of an unset variable)
    should hit the same guard as `run_date=None`.
    """
    _write_posting(data_root, "2026-04-25", "p1", verdict="accepted")
    with pytest.raises(UploadGuardError, match="--confirm"):
        push_to_hub(run_date="", dry_run=False, confirm=False)


# --- README counts cover all dates regardless of --date scope -----------


def test_scoped_run_regenerates_readme_with_all_remote_dates(
    data_root: Path, stub_hf: dict
) -> None:
    """A scoped `--date X` upload must preserve existing remote date Y counts.

    Scoped uploads stage only X's JSONL. Since `upload_folder` is additive and
    does not delete Y on HF, README counts must start from remote truth and
    overlay X's staged count.
    """
    # Y: previously uploaded date on HF, not restaged by this scoped upload.
    stub_hf["remote_files"]["data/2026-04-24.jsonl"] = '{"id":"p1"}\n{"id":"p2"}\n'
    # X: today's scoped upload
    _write_posting(data_root, "2026-04-25", "p3", verdict="accepted")

    push_to_hub(run_date="2026-04-25", dry_run=False)

    snapshot = stub_hf["upload_folder"][0]["_snapshot"]
    readme = snapshot["README.md"]

    # README counts line must mention both dates with the right tallies.
    assert "2026-04-25: 1" in readme
    assert "2026-04-24: 2" in readme

    # And the JSONL allow-pattern is still scoped to X — we don't accidentally
    # restage Y's data file (that would defeat the point of scoped uploads).
    assert stub_hf["upload_folder"][0]["allow_patterns"][0] == "data/2026-04-25.jsonl"
    # Only X's JSONL should have been staged (the allow_patterns scoping is
    # belt-and-braces but staging-only-X also matters for restage cost).
    assert "data/2026-04-25.jsonl" in snapshot
    assert "data/2026-04-24.jsonl" not in snapshot


def test_scoped_run_overlays_staged_count_over_existing_remote_count(
    data_root: Path, stub_hf: dict
) -> None:
    """The staged date's local accepted count replaces its old remote count."""
    stub_hf["remote_files"]["data/2026-04-24.jsonl"] = '{"id":"p1"}\n'
    stub_hf["remote_files"]["data/2026-04-25.jsonl"] = (
        '{"id":"old1"}\n{"id":"old2"}\n{"id":"old3"}\n'
    )
    _write_posting(data_root, "2026-04-25", "p3", verdict="accepted")
    _write_posting(data_root, "2026-04-25", "p4", verdict="rejected")

    push_to_hub(run_date="2026-04-25", dry_run=False)

    readme = stub_hf["upload_folder"][0]["_snapshot"]["README.md"]
    assert "2026-04-24: 1" in readme
    assert "2026-04-25: 1" in readme
    assert "2026-04-25: 3" not in readme


def test_scoped_run_preserves_remote_counts_when_local_history_absent(
    data_root: Path, stub_hf: dict
) -> None:
    """Backfill temp worktrees should not collapse README counts to one date."""
    stub_hf["remote_files"]["data/2026-04-24.jsonl"] = '{"id":"p1"}\n{"id":"p2"}\n'
    stub_hf["remote_files"]["data/2026-05-09.jsonl"] = '{"id":"p3"}\n'

    _write_posting(data_root, "2026-05-10", "p4", verdict="accepted")

    push_to_hub(run_date="2026-05-10", dry_run=False)

    readme = stub_hf["upload_folder"][0]["_snapshot"]["README.md"]
    assert "2026-05-10: 1" in readme
    assert "2026-05-09: 1" in readme
    assert "2026-04-24: 2" in readme
