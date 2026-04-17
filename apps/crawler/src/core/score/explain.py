"""LLM-generated fit explanation for a job match."""

from __future__ import annotations

import structlog

from src.core.score.resume import ResumeParsed

log = structlog.get_logger()

_EXPLAIN_SYSTEM = """\
You are a career advisor. Given a candidate profile and a job posting, write 2-3 sentences \
explaining why this job is a good match. Be specific — mention shared technologies or domain \
overlap. Be honest if the match is weak.
"""

_EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
}


async def explain_match(
    resume: ResumeParsed,
    job: dict,
    provider,
) -> str:
    """Return a 2-3 sentence fit explanation.

    provider — SyncProvider instance with:
        async def generate(system_prompt, user_content, response_schema) -> tuple[dict, LLMUsage]

    The caller is responsible for rate limiting between calls (sleep 60/rpm seconds).
    """
    enrichment = job.get("enrichment") or {}

    user_msg = "\n".join([
        "Candidate profile:",
        f"  Occupation: {resume.occupation or 'not specified'}",
        f"  Experience: {resume.experience_years or 'not specified'} years",
        f"  Technologies: {', '.join(resume.technologies) or 'none listed'}",
        f"  Keywords: {', '.join(resume.keywords) or 'none listed'}",
        "",
        "Job posting:",
        f"  Title: {job.get('title') or 'Unknown'}",
        f"  Company: {job.get('company_name') or 'Unknown'}",
        f"  Technologies: {', '.join(enrichment.get('technologies') or []) or 'none listed'}",
        f"  Keywords: {', '.join(enrichment.get('keywords') or []) or 'none listed'}",
        f"  Seniority: {enrichment.get('seniority') or 'not specified'}",
    ])

    result_dict, usage = await provider.generate(
        system_prompt=_EXPLAIN_SYSTEM,
        user_content=user_msg,
        response_schema=_EXPLAIN_SCHEMA,
    )
    log.info(
        "score.explain_call",
        title=job.get("title"),
        company=job.get("company_name"),
        input_tokens=usage.input_tokens if usage else None,
    )
    return result_dict.get("explanation", "")
