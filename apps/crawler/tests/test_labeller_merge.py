"""Tests for merge_posting — accepted (strict) vs rejected (tolerant) paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.labeller.merge import merge_posting


def _input_json() -> dict:
    return {
        "id": "abc-123",
        "schema_version": 1,
        "crawler_version": "0.0.1",
        "normalizer_version": "deterministic-v1",
        "sampled_at": "2026-04-25T00:00:00+00:00",
        "source": {"source_url": "https://example.com/job/1"},
        "input": {
            "title_raw": "Example Role",
            "description_html": "<p>x</p>",
            "description_text": "x",
            "blocks": [{"id": 0, "tag": "p", "html": "<p>x</p>", "text": "x"}],
        },
    }


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LABELLER_DATA_ROOT", str(tmp_path))
    base = tmp_path / "_runs" / "2026-04-25" / "abc-123"
    base.mkdir(parents=True)
    (base / "input.json").write_text(json.dumps(_input_json()))
    return base


# ---------- merged-mode happy path --------------------------------------


def test_merge_uses_extract_all_when_present(run_dir: Path) -> None:
    (run_dir / "extract-all-out.json").write_text(
        json.dumps(
            {
                "sections": [{"kind": "role", "block_ids": [0], "extracted": {"summary": "x"}}],
                "globals": {"profession": "engineer"},
            }
        )
    )
    out = merge_posting("2026-04-25", "abc-123")
    assert out["labels"]["globals"] == {"profession": "engineer"}
    assert out["labels"]["sections"][0]["extracted"] == {"summary": "x"}
    assert out["labelling_meta"]["qa_verdict"] == "accepted"


# ---------- granular-mode strict (accepted) raises on missing -----------


def test_accepted_merge_raises_on_missing_split(run_dir: Path) -> None:
    """No extract-all-out.json + no split-out.json → strict mode raises."""
    with pytest.raises(FileNotFoundError):
        merge_posting("2026-04-25", "abc-123")


def test_accepted_merge_raises_on_missing_per_kind(run_dir: Path) -> None:
    (run_dir / "split-out.json").write_text(
        json.dumps({"sections": [{"kind": "role", "block_ids": [0]}]})
    )
    (run_dir / "globals-out.json").write_text(json.dumps({"profession": "x"}))
    # extract-role-out.json deliberately absent.
    with pytest.raises(FileNotFoundError, match="extract-role-out.json"):
        merge_posting("2026-04-25", "abc-123")


# ---------- rejected fallback synthesizes shells ------------------------


def test_rejected_merge_synthesizes_when_split_and_globals_absent(
    run_dir: Path,
) -> None:
    """The orchestrator's fallback path: nothing landed except input.json.
    The rejected merge must succeed and stamp the rationale."""
    out = merge_posting("2026-04-25", "abc-123", qa_verdict="rejected")
    assert out["labelling_meta"]["qa_verdict"] == "rejected"
    assert out["labels"]["sections"] == []
    assert out["labels"]["globals"] == {}
    rationale = out["labelling_meta"]["qa_rationale"]
    assert rationale is not None
    assert "split-out.json" in rationale
    assert "globals-out.json" in rationale


def test_rejected_merge_uses_partial_outputs_when_some_files_present(
    run_dir: Path,
) -> None:
    """A partial run that produced split + globals but crashed before role
    extract: rejected merge keeps what's there + nulls the missing extract."""
    (run_dir / "split-out.json").write_text(
        json.dumps(
            {
                "sections": [
                    {"kind": "role", "block_ids": [0]},
                    {"kind": "team", "block_ids": [1]},
                ]
            }
        )
    )
    (run_dir / "globals-out.json").write_text(json.dumps({"profession": "x"}))
    (run_dir / "extract-team-out.json").write_text(json.dumps({"team_name": "platform"}))
    # extract-role-out.json missing.

    out = merge_posting("2026-04-25", "abc-123", qa_verdict="rejected")
    assert out["labels"]["globals"] == {"profession": "x"}
    sections = {s["kind"]: s for s in out["labels"]["sections"]}
    assert sections["role"]["extracted"] is None
    assert sections["team"]["extracted"] == {"team_name": "platform"}
    rationale = out["labelling_meta"]["qa_rationale"]
    assert "extract-role-out.json" in rationale
    assert "split-out.json" not in rationale  # was present


def test_rejected_merge_preserves_caller_rationale(run_dir: Path) -> None:
    """Caller-supplied rationale (e.g. the QA rule that failed) is preserved
    and the missing-files note is appended."""
    out = merge_posting(
        "2026-04-25",
        "abc-123",
        qa_verdict="rejected",
        qa_rationale="qa.split_coverage: 12% < 40%",
    )
    rationale = out["labelling_meta"]["qa_rationale"]
    assert rationale.startswith("qa.split_coverage: 12% < 40%")
    assert "partial pipeline failure" in rationale


def test_rejected_merge_with_complete_files_does_not_add_note(
    run_dir: Path,
) -> None:
    """If all files are present, the rejected-merge codepath should not
    invent a missing-files note."""
    (run_dir / "extract-all-out.json").write_text(json.dumps({"sections": [], "globals": {}}))
    out = merge_posting(
        "2026-04-25",
        "abc-123",
        qa_verdict="rejected",
        qa_rationale="qa.profession_empty",
    )
    assert out["labelling_meta"]["qa_rationale"] == "qa.profession_empty"


# ---------- accepted merge stays strict in the same fallback paths -----


def test_accepted_merge_does_not_synthesize_globals(run_dir: Path) -> None:
    (run_dir / "split-out.json").write_text(json.dumps({"sections": []}))
    # globals-out.json absent.
    with pytest.raises(FileNotFoundError):
        merge_posting("2026-04-25", "abc-123")


# ---------- synthesized rejected record passes posting schema ----------


def test_rejected_synthesized_record_passes_posting_schema(run_dir: Path) -> None:
    """The whole point of the rejected fallback: caller can persist the record
    via the orchestrator's normal `validate --kind posting` step instead of
    hand-crafting JSON outside the pipeline."""
    from src.labeller.validate import validate_schema

    merged = merge_posting("2026-04-25", "abc-123", qa_verdict="rejected")
    assert validate_schema("posting", merged) == []


# ---------- malformed JSON tolerance on the rejected path --------------


def test_rejected_merge_tolerates_truncated_split_json(run_dir: Path) -> None:
    """A subagent that crashed mid-write may leave a truncated JSON file.
    The rejected-merge fallback must treat that the same as a missing file
    — otherwise the escape valve surfaces the very crash it's meant to
    recover from."""
    (run_dir / "split-out.json").write_text('{"sections": [{"kind": "rol')  # truncated
    out = merge_posting("2026-04-25", "abc-123", qa_verdict="rejected")
    assert out["labels"]["sections"] == []
    assert "split-out.json" in out["labelling_meta"]["qa_rationale"]


def test_rejected_merge_tolerates_truncated_per_kind_extract(run_dir: Path) -> None:
    (run_dir / "split-out.json").write_text(
        json.dumps({"sections": [{"kind": "role", "block_ids": [0]}]})
    )
    (run_dir / "globals-out.json").write_text(json.dumps({"profession": "x"}))
    (run_dir / "extract-role-out.json").write_text("{trun")  # truncated
    out = merge_posting("2026-04-25", "abc-123", qa_verdict="rejected")
    sections = {s["kind"]: s for s in out["labels"]["sections"]}
    assert sections["role"]["extracted"] is None
    assert "extract-role-out.json" in out["labelling_meta"]["qa_rationale"]


def test_accepted_merge_does_not_swallow_malformed_json(run_dir: Path) -> None:
    """Strict mode must NOT silently treat a truncated file as missing."""
    (run_dir / "split-out.json").write_text('{"sections": [{"kind"')
    with pytest.raises(json.JSONDecodeError):
        merge_posting("2026-04-25", "abc-123")


# ---------- input.json is non-negotiable in either mode -----------------


def test_rejected_merge_still_requires_input_json(tmp_path: Path, monkeypatch) -> None:
    """input.json carries the posting identity (id, source, sampled_at).
    Even rejected mode cannot synthesize that — failure to find it is a
    hard error in both verdicts."""
    monkeypatch.setenv("LABELLER_DATA_ROOT", str(tmp_path))
    base = tmp_path / "_runs" / "2026-04-25" / "abc-123"
    base.mkdir(parents=True)
    # input.json deliberately absent.
    with pytest.raises(FileNotFoundError):
        merge_posting("2026-04-25", "abc-123", qa_verdict="rejected")
