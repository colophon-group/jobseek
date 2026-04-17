"""Tests for src/core/score/resume.py."""

from __future__ import annotations

import pytest

from src.core.score.resume import ResumeParsed, load_resume, resume_hash, save_resume


def test_roundtrip(tmp_path):
    parsed = ResumeParsed(
        technologies=["Python", "PostgreSQL"],
        keywords=["backend", "data engineering"],
        experience_years=2,
        education="bachelor",
        occupation="Software Engineer",
    )
    path = tmp_path / "resume-parsed.yaml"
    save_resume(parsed, path)
    loaded = load_resume(path)
    assert loaded == parsed


def test_hash_is_stable(tmp_path):
    parsed = ResumeParsed(technologies=["Python"], keywords=["backend"], experience_years=1)
    path = tmp_path / "resume-parsed.yaml"
    save_resume(parsed, path)
    h1 = resume_hash(load_resume(path))
    h2 = resume_hash(load_resume(path))
    assert h1 == h2


def test_hash_changes_on_content_update():
    p1 = ResumeParsed(technologies=["Python"])
    p2 = ResumeParsed(technologies=["Python", "Go"])
    assert resume_hash(p1) != resume_hash(p2)


def test_empty_resume_defaults():
    parsed = ResumeParsed()
    assert parsed.technologies == []
    assert parsed.keywords == []
    assert parsed.experience_years is None
    assert parsed.education is None
    assert parsed.occupation is None
    assert len(resume_hash(parsed)) == 64  # sha256 hex = 64 chars


def test_invalid_education_raises():
    with pytest.raises(Exception):
        ResumeParsed(education="phd")  # not in Education literal


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_resume(tmp_path / "nonexistent.yaml")


def test_save_creates_parent_dirs(tmp_path):
    parsed = ResumeParsed(technologies=["Python"])
    path = tmp_path / "nested" / "dir" / "resume.yaml"
    save_resume(parsed, path)
    assert path.exists()
