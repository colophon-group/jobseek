"""Resume parsing, YAML I/O, and hash utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

Education = Literal["none", "vocational", "associate", "bachelor", "master", "doctorate"]

_PARSE_SYSTEM_PROMPT = """\
You are a structured data extractor for resumes. Extract a skills profile from the resume text.

Rules:
- Extract only what is explicitly stated. Do not guess.
- Return null for any field where the information is absent.

Field guidance:
technologies — Specific named tools, frameworks, and languages. Use proper casing (e.g. "PostgreSQL" \
not "postgres", "React" not "react"). Do not include generic categories like "databases" or "cloud".
keywords — 5-10 lowercase terms describing the candidate's role function, domain, and industry. \
Do NOT include technology names (those go in technologies).
experience_years — Total years of professional experience as a single integer. Null if not determinable.
education — Highest completed degree: "none", "vocational", "associate", "bachelor", "master", \
"doctorate". Null if not stated.
occupation — Primary job function in English, without seniority qualifiers. \
E.g. "Software Engineer", "Data Analyst".
"""


class ResumeParsed(BaseModel):
    technologies: list[str] = []
    keywords: list[str] = []
    experience_years: int | None = None
    education: Education | None = None
    occupation: str | None = None


def load_resume(path: str | Path) -> ResumeParsed:
    """Load ai/resume-parsed.yaml into ResumeParsed. Raises FileNotFoundError if missing."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ResumeParsed.model_validate(data or {})


def save_resume(parsed: ResumeParsed, path: str | Path) -> None:
    """Write ResumeParsed to YAML (creates parent dirs as needed)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.dump(parsed.model_dump(), default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )


def resume_hash(parsed: ResumeParsed) -> str:
    """Stable sha256 of the sorted YAML serialization. Changes when resume content changes."""
    content = yaml.dump(parsed.model_dump(), default_flow_style=False, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def _extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF using pypdf (already in base deps)."""
    from pypdf import PdfReader  # noqa: PLC0415

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def parse_resume_with_llm(source_path: str | Path, provider) -> ResumeParsed:
    """Read a LaTeX/PDF/plain-text resume, call Gemini, return ResumeParsed.

    provider — SyncProvider instance (GeminiSyncProvider from Phase 1).
    Raises ValueError if no text can be extracted.
    """
    path = Path(source_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        text = _extract_pdf_text(path)
    else:
        # .tex, .md, .txt — read directly; LLM ignores LaTeX markup
        text = path.read_text(encoding="utf-8")

    if not text.strip():
        raise ValueError(f"No text extracted from {path}. If PDF, try converting to LaTeX first.")

    response_schema = ResumeParsed.model_json_schema()

    result_dict, _ = await provider.generate(
        system_prompt=_PARSE_SYSTEM_PROMPT,
        user_content=f"Resume:\n\n{text[:40_000]}",
        response_schema=response_schema,
    )
    return ResumeParsed.model_validate(result_dict)
