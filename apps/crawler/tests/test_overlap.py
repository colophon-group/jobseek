"""Tests for src/core/score/overlap.py."""

from __future__ import annotations

import pytest

from src.core.score.overlap import compute_overlap
from src.core.score.resume import ResumeParsed


def _job(technologies=None, keywords=None, seniority=None):
    return {
        "enrichment": {
            "technologies": technologies or [],
            "keywords": keywords or [],
            "seniority": seniority,
        }
    }


def test_full_tech_and_keyword_match():
    resume = ResumeParsed(technologies=["Python", "PostgreSQL"], keywords=["backend", "api"])
    job = _job(technologies=["Python", "PostgreSQL"], keywords=["backend", "api"])
    score = compute_overlap(resume, job)
    # tech=1.0*0.5, kw=1.0*0.3, seniority=None→1.0*0.2 → 100.0
    assert score == 100.0


def test_zero_tech_and_keyword_match():
    resume = ResumeParsed(technologies=["Python"], keywords=["backend"])
    job = _job(technologies=["Java"], keywords=["finance"])
    score = compute_overlap(resume, job)
    # tech=0, kw=0, seniority=None→1.0*0.2 → 20.0
    assert score == 20.0


def test_partial_tech_overlap():
    resume = ResumeParsed(technologies=["Python", "Go", "React"])
    job = _job(technologies=["Python", "Java"])
    score = compute_overlap(resume, job)
    # tech=1/2=0.5*0.5=0.25, kw=0, seniority=None→1.0*0.2 → 45.0
    assert score == 45.0


def test_seniority_penalty_for_senior():
    resume = ResumeParsed(technologies=[], keywords=[])
    job = _job(seniority="senior")
    score = compute_overlap(resume, job)
    # tech=0, kw=0, seniority=0.5*0.2 → 10.0
    assert score == 10.0


def test_seniority_penalty_for_director():
    resume = ResumeParsed(technologies=[], keywords=[])
    job = _job(seniority="director")
    score = compute_overlap(resume, job)
    assert score == 10.0


def test_seniority_ok_for_entry():
    resume = ResumeParsed(technologies=[], keywords=[])
    job = _job(seniority="entry")
    score = compute_overlap(resume, job)
    assert score == 20.0


def test_seniority_ok_for_intern():
    resume = ResumeParsed(technologies=[], keywords=[])
    job = _job(seniority="intern")
    score = compute_overlap(resume, job)
    assert score == 20.0


def test_null_seniority_is_ok():
    resume = ResumeParsed(technologies=[], keywords=[])
    job = _job(seniority=None)
    score = compute_overlap(resume, job)
    assert score == 20.0


def test_case_insensitive_match():
    resume = ResumeParsed(technologies=["python", "postgresql"], keywords=["Backend"])
    job = _job(technologies=["Python", "PostgreSQL"], keywords=["backend"])
    score = compute_overlap(resume, job)
    assert score == 100.0


def test_empty_resume_technologies():
    resume = ResumeParsed(technologies=[])
    job = _job(technologies=["Python", "Go"])
    score = compute_overlap(resume, job)
    # tech=0/2=0, seniority=None→1.0*0.2 → 20.0
    assert score == 20.0


def test_null_enrichment_field():
    resume = ResumeParsed(technologies=["Python"], keywords=["backend"])
    job = {"enrichment": None}
    score = compute_overlap(resume, job)
    assert score == 20.0


def test_missing_enrichment_key():
    resume = ResumeParsed(technologies=["Python"])
    job = {}
    score = compute_overlap(resume, job)
    assert score == 20.0


def test_score_is_in_range():
    resume = ResumeParsed(technologies=["Python"] * 10, keywords=["backend"] * 5)
    job = _job(technologies=["Python"] * 10, keywords=["backend"] * 5, seniority="entry")
    score = compute_overlap(resume, job)
    assert 0.0 <= score <= 100.0
