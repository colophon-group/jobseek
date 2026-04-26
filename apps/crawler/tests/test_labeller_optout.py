"""Tests for the labeller opt-out company filter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.labeller.upload import _accepted_by_date, _load_optout


def _write_posting(
    root: Path, date: str, posting_id: str, *, slug: str, verdict: str = "accepted"
) -> Path:
    p = root / "postings" / date / f"{posting_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "id": posting_id,
                "labelling_meta": {"qa_verdict": verdict},
                "source": {"company_slug": slug},
            }
        )
    )
    return p


@pytest.fixture
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LABELLER_DATA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def optout_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch optout_file() to point at a per-test file."""
    target = tmp_path / "labeller_optout.txt"
    monkeypatch.setattr("src.labeller.upload.optout_file", lambda: target)
    return target


# --- _load_optout --------------------------------------------------------


def test_load_optout_missing_file_returns_empty_set(optout_path: Path) -> None:
    assert _load_optout() == set()


def test_load_optout_skips_blank_lines_and_comments(optout_path: Path) -> None:
    optout_path.write_text(
        "# header comment\n"
        "\n"
        "acme\n"
        "  widgets-corp  \n"  # whitespace trimmed
        "# inline reason: takedown 2026-04-30\n"
        "evil-megacorp\n"
    )
    assert _load_optout() == {"acme", "widgets-corp", "evil-megacorp"}


def test_load_optout_lowercases_entries(optout_path: Path) -> None:
    """Slugs are normalised to lowercase on load.

    companies.csv slugs are lowercase by convention; accepting an upper-case
    typo in the opt-out file would silently fail to match the posting's
    `source.company_slug`. Lowercasing on both sides avoids that foot-gun.
    """
    optout_path.write_text("Acme\nWidgets-Corp\n")
    assert _load_optout() == {"acme", "widgets-corp"}


# --- _accepted_by_date integration ---------------------------------------


def test_accepted_includes_postings_when_no_optout(
    isolated_data_root: Path, optout_path: Path
) -> None:
    _write_posting(isolated_data_root, "2026-04-25", "p1", slug="acme")
    out = _accepted_by_date(None)
    assert out["2026-04-25"][0]["id"] == "p1"


def test_accepted_filters_postings_with_optedout_slug(
    isolated_data_root: Path, optout_path: Path
) -> None:
    optout_path.write_text("acme\n")
    _write_posting(isolated_data_root, "2026-04-25", "p1", slug="acme")
    _write_posting(isolated_data_root, "2026-04-25", "p2", slug="widgets-corp")
    out = _accepted_by_date(None)
    ids = [r["id"] for r in out["2026-04-25"]]
    assert ids == ["p2"]


def test_accepted_drops_date_when_all_postings_optedout(
    isolated_data_root: Path, optout_path: Path
) -> None:
    """A date with no surviving postings should not appear in the output."""
    optout_path.write_text("acme\n")
    _write_posting(isolated_data_root, "2026-04-25", "p1", slug="acme")
    _write_posting(isolated_data_root, "2026-04-26", "p2", slug="widgets-corp")
    out = _accepted_by_date(None)
    assert "2026-04-25" not in out
    assert [r["id"] for r in out["2026-04-26"]] == ["p2"]


def test_accepted_does_not_filter_rejected_postings(
    isolated_data_root: Path, optout_path: Path
) -> None:
    """Opt-out filter is downstream of qa_verdict — rejected postings already excluded."""
    optout_path.write_text("acme\n")
    _write_posting(isolated_data_root, "2026-04-25", "p1", slug="acme", verdict="rejected")
    _write_posting(isolated_data_root, "2026-04-25", "p2", slug="widgets-corp", verdict="accepted")
    out = _accepted_by_date(None)
    assert [r["id"] for r in out["2026-04-25"]] == ["p2"]


def test_accepted_handles_missing_company_slug_field(
    isolated_data_root: Path, optout_path: Path
) -> None:
    """A posting with no source.company_slug should not match any opt-out entry."""
    optout_path.write_text("acme\n")
    p = isolated_data_root / "postings" / "2026-04-25" / "p1.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": "p1", "labelling_meta": {"qa_verdict": "accepted"}}))
    out = _accepted_by_date(None)
    assert [r["id"] for r in out["2026-04-25"]] == ["p1"]


def test_accepted_filter_is_case_insensitive(isolated_data_root: Path, optout_path: Path) -> None:
    """An upper-case slug in the file still matches a lower-case posting slug."""
    optout_path.write_text("Acme\n")
    _write_posting(isolated_data_root, "2026-04-25", "p1", slug="acme")
    _write_posting(isolated_data_root, "2026-04-25", "p2", slug="widgets-corp")
    out = _accepted_by_date(None)
    assert [r["id"] for r in out["2026-04-25"]] == ["p2"]
