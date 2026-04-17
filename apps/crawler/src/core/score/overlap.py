"""Pure-Python overlap score between a parsed resume and an enriched job.

No I/O. No LLM calls. Fully deterministic and unit-testable.
"""

from __future__ import annotations

from src.core.score.resume import ResumeParsed

# Seniority values that are a good fit for an early-career resume.
# None (unknown) is treated as acceptable.
_PREFERRED_SENIORITY: frozenset[str | None] = frozenset({"intern", "entry", "mid", None})

# Component weights — must sum to 1.0.
_TECH_WEIGHT = 0.5
_KW_WEIGHT = 0.3
_SENIORITY_WEIGHT = 0.2


def compute_overlap(resume: ResumeParsed, job: dict) -> float:
    """Return a 0–100 overlap score.

    job must have an 'enrichment' key (dict or None) with optional
    'technologies' (list[str]), 'keywords' (list[str]), and 'seniority' (str | None).
    """
    enrichment = job.get("enrichment") or {}

    job_techs = {t.lower() for t in (enrichment.get("technologies") or [])}
    job_kws = {k.lower() for k in (enrichment.get("keywords") or [])}
    job_seniority: str | None = enrichment.get("seniority")

    resume_techs = {t.lower() for t in resume.technologies}
    resume_kws = {k.lower() for k in resume.keywords}

    tech_score = len(resume_techs & job_techs) / max(len(job_techs), 1)
    kw_score = len(resume_kws & job_kws) / max(len(job_kws), 1)
    seniority_score = 1.0 if job_seniority in _PREFERRED_SENIORITY else 0.5

    raw = tech_score * _TECH_WEIGHT + kw_score * _KW_WEIGHT + seniority_score * _SENIORITY_WEIGHT
    return round(raw * 100, 2)
