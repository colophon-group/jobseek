# Resume Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `crawler parse-resume` and `crawler score` commands that rank enriched job postings against the user's resume using structured overlap scoring + Gemini explanations for the top matches.

**Architecture:** A `core/score/` module provides three focused units: `resume.py` (parse/load/hash), `overlap.py` (pure-Python scoring), and `explain.py` (LLM explanation). A new `queries/score.py` handles DB reads/writes. Two new CLI commands wire everything together. The existing `queries/alert.py` (from Phase 1) is extended to join and sort by score.

**Tech Stack:** asyncpg, pydantic, pyyaml, pypdf (already installed), google-genai (already in `enrich` extras), structlog, alembic

---

## Prerequisites

**Phase 1 (`2026-04-16-local-job-alert-filter-design.md`) must be complete before starting this plan.** This plan assumes the following exist:

- `apps/crawler/src/core/enrich/providers/gemini_sync.py` — `GeminiSyncProvider` with `async def generate(system_prompt, user_content, response_schema) -> tuple[dict, LLMUsage]`
- `apps/crawler/src/core/enrich/providers/__init__.py` — `SyncProvider` Protocol + `create_sync_provider(provider, model, api_key) -> SyncProvider` factory
- `apps/crawler/src/core/enrich/local.py` — `mark_candidates_from_yaml`, `run_sync_enrich`
- `apps/crawler/src/queries/alert.py` — alert query returning enriched job rows
- `apps/crawler/src/migrations/versions/0004_add_enrich_batch_table.py` — already run
- `apps/crawler/src/config.py` — `enrich_rate_limit_rpm: int = 15` setting added by Phase 1
- `ai/filters.yaml` — has `exclude_title_patterns`, `require.experience_max`, `output.limit`

---

## File Map

| Path | Action | Responsibility |
|------|--------|----------------|
| `src/core/score/__init__.py` | Create | Package marker |
| `src/core/score/resume.py` | Create | ResumeParsed model, YAML I/O, hash, LLM parse |
| `src/core/score/overlap.py` | Create | Pure-Python overlap score computation |
| `src/core/score/explain.py` | Create | LLM fit explanation, rate-limited |
| `src/queries/score.py` | Create | DB reads/writes for resume_score table |
| `src/migrations/versions/0005_add_resume_score_table.py` | Create | Alembic migration |
| `tests/test_resume_parse.py` | Create | Unit tests for resume.py |
| `tests/test_overlap.py` | Create | Unit tests for overlap.py |
| `src/cli.py` | Modify | Add `parse-resume` and `score` subcommands |
| `src/queries/alert.py` | Modify | Add LEFT JOIN resume_score + ORDER BY overlap_score |
| `ai/filters.yaml` | Modify | Add `score.explain_top_n: 20` |
| `.gitignore` (repo root) | Modify | Ignore `ai/resume-parsed.yaml` |

---

## Task 1: Add `resume_score` migration

**Files:**
- Create: `apps/crawler/src/migrations/versions/0005_add_resume_score_table.py`

- [ ] **Step 1: Create the migration file**

```python
"""Add resume_score table.

Revision ID: 0005
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS resume_score (
            posting_id    uuid PRIMARY KEY REFERENCES job_posting(id) ON DELETE CASCADE,
            resume_hash   text NOT NULL,
            overlap_score numeric(5,2) NOT NULL,
            explanation   text,
            scored_at     timestamptz DEFAULT now(),
            explained_at  timestamptz
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_resume_score_overlap "
        "ON resume_score(overlap_score DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS resume_score")
```

- [ ] **Step 2: Run the migration**

```bash
cd apps/crawler
uv run alembic upgrade head
```

Expected: `Running upgrade 0004 -> 0005, Add resume_score table`

- [ ] **Step 3: Verify the table exists**

```bash
uv run python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://crawler:crawler@localhost:5432/crawler')
    row = await conn.fetchrow(\"SELECT column_name FROM information_schema.columns WHERE table_name = 'resume_score' ORDER BY ordinal_position LIMIT 1\")
    print(row)
    await conn.close()
asyncio.run(check())
"
```

Expected: prints a row with `column_name = 'posting_id'`

- [ ] **Step 4: Commit**

```bash
git add apps/crawler/src/migrations/versions/0005_add_resume_score_table.py
git commit -m "feat: add resume_score migration"
```

---

## Task 2: Create `core/score/resume.py`

**Files:**
- Create: `apps/crawler/src/core/score/__init__.py`
- Create: `apps/crawler/src/core/score/resume.py`

- [ ] **Step 1: Create the package marker**

```python
# apps/crawler/src/core/score/__init__.py
```

(Empty file — just marks this as a Python package.)

- [ ] **Step 2: Create `resume.py`**

```python
# apps/crawler/src/core/score/resume.py
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

    # provider.generate() is defined by the SyncProvider Protocol (Phase 1):
    #   async def generate(system_prompt: str, user_content: str,
    #                      response_schema: dict) -> tuple[dict, LLMUsage]
    result_dict, _ = await provider.generate(
        system_prompt=_PARSE_SYSTEM_PROMPT,
        user_content=f"Resume:\n\n{text[:40_000]}",
        response_schema=response_schema,
    )
    return ResumeParsed.model_validate(result_dict)
```

- [ ] **Step 3: Commit**

```bash
git add apps/crawler/src/core/score/
git commit -m "feat: add ResumeParsed model and YAML I/O"
```

---

## Task 3: Tests for `resume.py`

**Files:**
- Create: `apps/crawler/tests/test_resume_parse.py`

- [ ] **Step 1: Write the tests**

```python
# apps/crawler/tests/test_resume_parse.py
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
    # hash should not raise on empty resume
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
    save_resume(parsed, path)  # should not raise
    assert path.exists()
```

- [ ] **Step 2: Run the tests**

```bash
cd apps/crawler
uv run pytest tests/test_resume_parse.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/crawler/tests/test_resume_parse.py
git commit -m "test: resume parse model roundtrip and hash tests"
```

---

## Task 4: Create `core/score/overlap.py`

**Files:**
- Create: `apps/crawler/src/core/score/overlap.py`

- [ ] **Step 1: Write the failing tests first** (TDD — overlap tests come before implementation)

Create `apps/crawler/tests/test_overlap.py`:

```python
# apps/crawler/tests/test_overlap.py
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
    # seniority=0.5 weight → 10.0
    assert score == 10.0


def test_seniority_ok_for_entry():
    resume = ResumeParsed(technologies=[], keywords=[])
    job = _job(seniority="entry")
    score = compute_overlap(resume, job)
    # seniority=1.0*0.2 → 20.0
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
    # full match — case should not matter
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
    # enrichment=None → all zero except seniority=None → 20.0
    assert score == 20.0


def test_missing_enrichment_key():
    resume = ResumeParsed(technologies=["Python"])
    job = {}  # no "enrichment" key at all
    score = compute_overlap(resume, job)
    assert score == 20.0


def test_score_is_in_range():
    resume = ResumeParsed(technologies=["Python"] * 10, keywords=["backend"] * 5)
    job = _job(technologies=["Python"] * 10, keywords=["backend"] * 5, seniority="entry")
    score = compute_overlap(resume, job)
    assert 0.0 <= score <= 100.0
```

- [ ] **Step 2: Run tests to verify they fail** (module doesn't exist yet)

```bash
cd apps/crawler
uv run pytest tests/test_overlap.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.core.score.overlap'`

- [ ] **Step 3: Implement `overlap.py`**

```python
# apps/crawler/src/core/score/overlap.py
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
```

- [ ] **Step 4: Run tests — expect all to pass**

```bash
uv run pytest tests/test_overlap.py -v
```

Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/crawler/src/core/score/overlap.py apps/crawler/tests/test_overlap.py
git commit -m "feat: add overlap scoring + tests"
```

---

## Task 5: Create `queries/score.py`

**Files:**
- Create: `apps/crawler/src/queries/score.py`

- [ ] **Step 1: Create the file**

```python
# apps/crawler/src/queries/score.py
"""DB queries for resume_score table."""

from __future__ import annotations

from typing import Any

import asyncpg

# Fetch jobs that need scoring:
#   - active + enriched + passed cheap filters
#   - not yet scored OR scored with a different resume hash
_FETCH_UNSCORED = """
    SELECT
        jp.id          AS posting_id,
        jp.titles[1]   AS title,
        jp.enrichment,
        c.name         AS company_name,
        jp.first_seen_at
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    LEFT JOIN resume_score rs ON rs.posting_id = jp.id
    WHERE jp.is_active = true
      AND jp.enrichment IS NOT NULL
      AND jp.to_be_enriched = false
      AND jp.titles[1] !~* $1
      AND (jp.experience_max IS NULL OR jp.experience_max <= $2)
      AND (rs.posting_id IS NULL OR rs.resume_hash != $3)
    ORDER BY jp.first_seen_at DESC
"""

_UPSERT_SCORE = """
    INSERT INTO resume_score (posting_id, resume_hash, overlap_score, scored_at)
    VALUES ($1, $2, $3, now())
    ON CONFLICT (posting_id) DO UPDATE
        SET resume_hash   = EXCLUDED.resume_hash,
            overlap_score = EXCLUDED.overlap_score,
            scored_at     = now(),
            explanation   = NULL,
            explained_at  = NULL
"""

_UPSERT_EXPLANATION = """
    UPDATE resume_score
    SET explanation  = $1,
        explained_at = now()
    WHERE posting_id = $2
"""


async def fetch_unscored_jobs(
    conn: asyncpg.Connection,
    *,
    resume_hash: str,
    exclude_title_regex: str,
    experience_max: int | None,
) -> list[dict[str, Any]]:
    """Return unscored (or stale-hash) jobs as plain dicts.

    exclude_title_regex — passed as a !~* pattern, e.g. 'senior|lead|director'.
    experience_max      — max acceptable experience_max value; None → 9999 (any).
    """
    cap = experience_max if experience_max is not None else 9999
    rows = await conn.fetch(_FETCH_UNSCORED, exclude_title_regex, cap, resume_hash)
    return [dict(r) for r in rows]


async def upsert_score(
    conn: asyncpg.Connection,
    *,
    posting_id: str,
    resume_hash: str,
    overlap_score: float,
) -> None:
    """Insert or replace a score row. Clears explanation on re-score."""
    await conn.execute(_UPSERT_SCORE, posting_id, resume_hash, overlap_score)


async def upsert_explanation(
    conn: asyncpg.Connection,
    *,
    posting_id: str,
    explanation: str,
) -> None:
    """Write the LLM explanation text and set explained_at."""
    await conn.execute(_UPSERT_EXPLANATION, explanation, posting_id)
```

- [ ] **Step 2: Verify import works**

```bash
cd apps/crawler
uv run python -c "from src.queries.score import fetch_unscored_jobs; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/crawler/src/queries/score.py
git commit -m "feat: add resume_score DB queries"
```

---

## Task 6: Create `core/score/explain.py`

**Files:**
- Create: `apps/crawler/src/core/score/explain.py`

- [ ] **Step 1: Create the file**

```python
# apps/crawler/src/core/score/explain.py
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
```

- [ ] **Step 2: Verify import works**

```bash
cd apps/crawler
uv run python -c "from src.core.score.explain import explain_match; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/crawler/src/core/score/explain.py
git commit -m "feat: add LLM explain_match"
```

---

## Task 7: Add `parse-resume` to cli.py

**Files:**
- Modify: `apps/crawler/src/cli.py`

The existing CLI uses `argparse` (not Click). New subcommands follow the existing pattern: add a subparser in `parse_args()`, add an `elif` branch in `run()`.

- [ ] **Step 1: Add the subparser**

In `parse_args()`, after the last `sub.add_parser(...)` line and before `return parser.parse_args()`, add:

```python
    parse_resume_p = sub.add_parser(
        "parse-resume",
        help="Extract resume skills profile to ai/resume-parsed.yaml",
    )
    parse_resume_p.add_argument(
        "--resume",
        required=True,
        help="Path to resume file (.tex, .pdf, .txt, .md)",
    )
    parse_resume_p.add_argument(
        "--output",
        default="ai/resume-parsed.yaml",
        help="Output YAML path (default: ai/resume-parsed.yaml)",
    )
```

- [ ] **Step 2: Add the command handler**

In `run()`, before the final `else` / `close_all_pools` call, add:

```python
        elif args.command == "parse-resume":
            from src.core.enrich.providers import create_sync_provider
            from src.core.score.resume import parse_resume_with_llm, save_resume

            provider = create_sync_provider(
                settings.enrich_provider or "gemini",
                settings.enrich_model or "gemini-2.0-flash",
                settings.enrich_api_key,
            )
            parsed = await parse_resume_with_llm(args.resume, provider)
            save_resume(parsed, args.output)
            print(
                f"Resume parsed: {len(parsed.technologies)} technologies, "
                f"{len(parsed.keywords)} keywords → {args.output}"
            )
```

- [ ] **Step 3: Smoke-test the argument parser**

```bash
cd apps/crawler
uv run crawler parse-resume --help
```

Expected output includes: `--resume RESUME` and `--output OUTPUT`

- [ ] **Step 4: Commit**

```bash
git add apps/crawler/src/cli.py
git commit -m "feat: add parse-resume CLI command"
```

---

## Task 8: Add `score` to cli.py

**Files:**
- Modify: `apps/crawler/src/cli.py`

- [ ] **Step 1: Add the subparser**

In `parse_args()`, after the `parse-resume` subparser, add:

```python
    score_p = sub.add_parser(
        "score",
        help="Score filtered jobs against parsed resume",
    )
    score_p.add_argument(
        "--filters",
        default="ai/filters.yaml",
        help="Path to filters YAML (default: ai/filters.yaml)",
    )
    score_p.add_argument(
        "--resume",
        default="ai/resume-parsed.yaml",
        help="Path to parsed resume YAML (default: ai/resume-parsed.yaml)",
    )
```

- [ ] **Step 2: Add the command handler**

After the `parse-resume` elif block, add:

```python
        elif args.command == "score":
            import asyncio
            from pathlib import Path

            import yaml

            from src.core.enrich.providers import create_sync_provider
            from src.core.score.explain import explain_match
            from src.core.score.overlap import compute_overlap
            from src.core.score.resume import load_resume, resume_hash
            from src.queries.score import fetch_unscored_jobs, upsert_explanation, upsert_score

            resume_path = Path(args.resume)
            if not resume_path.exists():
                print(
                    f"Error: {resume_path} not found. "
                    "Run `crawler parse-resume --resume <path>` first."
                )
                raise SystemExit(1)

            filters_raw = yaml.safe_load(Path(args.filters).read_text(encoding="utf-8"))
            exclude_patterns = filters_raw.get("exclude_title_patterns") or []
            exclude_regex = "|".join(exclude_patterns) if exclude_patterns else "(?!)"
            experience_max = (filters_raw.get("require") or {}).get("experience_max")
            explain_top_n = (filters_raw.get("score") or {}).get("explain_top_n", 20)
            rate_limit_rpm: int = settings.enrich_rate_limit_rpm

            parsed = load_resume(resume_path)
            r_hash = resume_hash(parsed)

            local_pool = await create_local_pool()
            async with local_pool.acquire() as conn:
                jobs = await fetch_unscored_jobs(
                    conn,
                    resume_hash=r_hash,
                    exclude_title_regex=exclude_regex,
                    experience_max=experience_max,
                )

            if not jobs:
                print("No new jobs to score.")
                raise SystemExit(0)

            # Pure-Python overlap scoring — no API calls
            scored = [(job, compute_overlap(parsed, job)) for job in jobs]
            scored.sort(key=lambda x: x[1], reverse=True)

            # Persist all scores
            async with local_pool.acquire() as conn:
                for job, score in scored:
                    await upsert_score(
                        conn,
                        posting_id=str(job["posting_id"]),
                        resume_hash=r_hash,
                        overlap_score=score,
                    )

            # LLM explanations for top N only
            provider = create_sync_provider(
                settings.enrich_provider or "gemini",
                settings.enrich_model or "gemini-2.0-flash",
                settings.enrich_api_key,
            )

            top_jobs = scored[:explain_top_n]
            explained = 0
            for i, (job, _score) in enumerate(top_jobs):
                if i > 0:
                    await asyncio.sleep(60 / rate_limit_rpm)
                try:
                    explanation = await explain_match(parsed, job, provider)
                    async with local_pool.acquire() as conn:
                        await upsert_explanation(
                            conn,
                            posting_id=str(job["posting_id"]),
                            explanation=explanation,
                        )
                    explained += 1
                except Exception as exc:
                    log.warning(
                        "score.explain_failed",
                        posting_id=str(job["posting_id"]),
                        error=str(exc),
                    )

            top = scored[0]
            print(
                f"Scored {len(scored)} jobs. "
                f"Explained top {explained}. "
                f"Top match: {top[0].get('title')} @ {top[0].get('company_name')} "
                f"(score {top[1]})"
            )
```

- [ ] **Step 3: Smoke-test the argument parser**

```bash
cd apps/crawler
uv run crawler score --help
```

Expected output includes: `--filters FILTERS` and `--resume RESUME`

- [ ] **Step 4: Commit**

```bash
git add apps/crawler/src/cli.py
git commit -m "feat: add score CLI command"
```

---

## Task 9: Extend `queries/alert.py` with score join

**Files:**
- Modify: `apps/crawler/src/queries/alert.py`

This file was created by Phase 1. Open it and find the main `SELECT` statement. You need to:
1. Add `LEFT JOIN resume_score rs ON rs.posting_id = jp.id` after the existing `JOIN company` line
2. Add `rs.overlap_score, rs.explanation` to the `SELECT` list
3. Change `ORDER BY jp.first_seen_at DESC` to `ORDER BY rs.overlap_score DESC NULLS LAST, jp.first_seen_at DESC`

- [ ] **Step 1: Verify the current file**

```bash
cat apps/crawler/src/queries/alert.py
```

Identify the exact line with `ORDER BY` and the existing `JOIN company` line.

- [ ] **Step 2: Apply the three changes**

The modified SQL block should look like this (adapt to match the exact variable/string name used in Phase 1):

```python
_ALERT_QUERY = """
    SELECT
        jp.id,
        jp.titles[1]        AS title,
        jp.source_url,
        jp.first_seen_at,
        jp.experience_max,
        jp.enrichment,
        c.name              AS company_name,
        c.slug              AS company_slug,
        rs.overlap_score,
        rs.explanation
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    LEFT JOIN resume_score rs ON rs.posting_id = jp.id
    WHERE jp.is_active = true
      AND jp.enrichment->>'work_permit_support' = 'yes'
      AND (jp.experience_max IS NULL OR jp.experience_max <= $1)
      AND jp.titles[1] !~* $2
    ORDER BY rs.overlap_score DESC NULLS LAST, jp.first_seen_at DESC
    LIMIT $3
"""
```

> **Note:** The exact SELECT list and WHERE parameters in Phase 1's `alert.py` may differ slightly. Match the existing parameter positions ($1, $2, $3) and column list — only add the three changes listed above. Do not alter any existing logic.

- [ ] **Step 3: Verify import works**

```bash
cd apps/crawler
uv run python -c "from src.queries.alert import _ALERT_QUERY; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add apps/crawler/src/queries/alert.py
git commit -m "feat: extend alert query with resume_score join and ordering"
```

---

## Task 10: Update `ai/filters.yaml` and `.gitignore`

**Files:**
- Modify: `ai/filters.yaml`
- Modify: `.gitignore` (repo root, or create `ai/.gitignore`)

- [ ] **Step 1: Add score section to filters.yaml**

Open `ai/filters.yaml` and append:

```yaml
score:
  explain_top_n: 20   # number of top-overlap jobs to send to LLM for explanation
```

The full file should now be:

```yaml
# Personal job filters - phase 1
exclude_title_patterns:
  - senior
  - "sr\\."
  - principal
  - staff
  - lead
  - director
  - manager
  - "vp\\b"
  - "head of"

require:
  work_permit_support: "yes"
  experience_max: 2

output:
  limit: 100

score:
  explain_top_n: 20
```

- [ ] **Step 2: Add resume-parsed.yaml to .gitignore**

Check if the repo root `.gitignore` exists:

```bash
ls .gitignore
```

If it exists, add this line:

```
ai/resume-parsed.yaml
```

If it doesn't exist, create `ai/.gitignore` instead:

```
resume-parsed.yaml
```

- [ ] **Step 3: Commit**

```bash
git add ai/filters.yaml .gitignore
git commit -m "feat: add score config to filters.yaml, ignore resume-parsed.yaml"
```

---

## Task 11: End-to-end smoke test

No new files. Verifies the full Phase 2 pipeline runs without errors on real local data.

**Prerequisites:** Local Postgres running, Phase 1 pipeline run at least once (jobs enriched).

- [ ] **Step 1: Create a minimal test resume**

```bash
cat > /tmp/test-resume.tex << 'EOF'
\documentclass{article}
\begin{document}
\section*{Experience}
Software Engineer at Example Corp, 2 years.
Python, PostgreSQL, React, Docker.
Backend web development, REST APIs, data engineering.
Bachelor of Science in Computer Science.
\end{document}
EOF
```

- [ ] **Step 2: Parse the resume**

```bash
cd apps/crawler
ENRICH_PROVIDER=gemini ENRICH_MODEL=gemini-2.0-flash ENRICH_API_KEY=<your-key> \
  uv run crawler parse-resume --resume /tmp/test-resume.tex --output /tmp/test-resume-parsed.yaml
```

Expected: `Resume parsed: N technologies, M keywords → /tmp/test-resume-parsed.yaml`

Inspect the output:

```bash
cat /tmp/test-resume-parsed.yaml
```

Expected: YAML with `technologies: [Python, PostgreSQL, ...]` and `keywords: [backend, ...]`

- [ ] **Step 3: Run the score command**

```bash
ENRICH_PROVIDER=gemini ENRICH_MODEL=gemini-2.0-flash ENRICH_API_KEY=<your-key> \
  uv run crawler score --resume /tmp/test-resume-parsed.yaml --filters ai/filters.yaml
```

Expected: `Scored N jobs. Explained top M. Top match: <title> @ <company> (score X.X)`

- [ ] **Step 4: Verify scores in Postgres**

```bash
uv run python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://crawler:crawler@localhost:5432/crawler')
    rows = await conn.fetch('SELECT overlap_score, explanation IS NOT NULL AS has_exp FROM resume_score ORDER BY overlap_score DESC LIMIT 5')
    for r in rows:
        print(dict(r))
    await conn.close()
asyncio.run(check())
"
```

Expected: 5 rows with `overlap_score` values and `has_exp = True` for top matches.

- [ ] **Step 5: Run the full alert command**

```bash
uv run crawler alert --filters ai/filters.yaml
```

Expected: JSON output includes `overlap_score` and `explanation` fields, sorted by `overlap_score DESC NULLS LAST`.

- [ ] **Step 6: Run the full test suite**

```bash
cd apps/crawler
uv run pytest tests/test_resume_parse.py tests/test_overlap.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Final commit**

```bash
git add -u
git commit -m "feat: Phase 2 resume scoring complete — parse-resume + score commands"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Implemented in |
|---|---|
| `parse-resume` command (LaTeX + PDF) | Task 7, `resume.py` |
| Parse-once cache as `ai/resume-parsed.yaml` | Task 7, `save_resume()` |
| `resume_hash` for stale-detection | Task 2, `resume_hash()` |
| `resume_score` Postgres table | Task 1, migration 0005 |
| Hybrid scoring: structured overlap | Task 4, `overlap.py` |
| Hybrid scoring: LLM top-N explanation | Task 6, `explain.py` |
| `score` command wiring overlap + LLM + DB | Task 8 |
| `explain_top_n` from `ai/filters.yaml` | Task 8 (parsed from YAML in cli.py) |
| Rate-limited Gemini calls (15 RPM) | Task 8 (`asyncio.sleep(60/rpm)`) |
| `alert` extended with score join + sort | Task 9 |
| `ai/resume-parsed.yaml` gitignored | Task 10 |
| `score.explain_top_n: 20` in filters.yaml | Task 10 |
| Tests for `resume.py` | Task 3 |
| Tests for `overlap.py` | Task 4 (TDD — tests first) |

**Type consistency check:** `ResumeParsed` defined in Task 2, used consistently in Tasks 4, 6, 7, 8. `fetch_unscored_jobs` returns `list[dict]`; `compute_overlap` and `explain_match` both accept `dict` for job — consistent throughout. `upsert_score` / `upsert_explanation` take `posting_id: str` — matches `str(job["posting_id"])` in Task 8.

**Placeholder scan:** No TBD, TODO, or "implement later" present. All code shown in full. All commands shown with expected output.
