# Local Job Alert Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three CLI commands — `crawler mark-candidates`, `crawler enrich-local`, and `crawler alert` — that filter local job postings by title keywords and experience level, enrich them via Gemini sync calls, and output visa-sponsoring entry-level matches as JSON.

**Architecture:** Reuse `_persist_results` from the existing `batch.py` enrichment pipeline (handles taxonomy resolution, schema validation, DB writes). Add a local-mode claim query (no R2 requirement) and sync Gemini provider. Three idempotent CLI commands compose into a daily cron chain. All new code lives under `core/enrich/` and `queries/`.

**Tech Stack:** asyncpg, pydantic, pyyaml, google-genai (already in `enrich` extras), structlog, alembic, argparse

---

## Critical note on `batch.py`

`_persist_results` in `batch.py` at the end does:
```sql
UPDATE enrich_batch SET status = 'completed', ... WHERE id = $1
```
This means the `enrich_batch` row **must exist** before calling `_persist_results`. The local sync loop inserts a synthetic row before each call.

The existing `_CLAIM_PENDING` in `batch.py` requires `description_r2_hash IS NOT NULL` — that blocks locally since descriptions live in the `descriptions` table, not R2. The local claim query in this plan removes that constraint.

---

## File Map

| Path | Action | Responsibility |
|------|--------|----------------|
| `src/migrations/versions/0004_add_enrich_batch_table.py` | Create | Alembic migration for `enrich_batch` table |
| `src/config.py` | Modify | Add 4 new local-mode settings |
| `apps/crawler/env.local.example` | Modify | Document new env vars |
| `src/core/enrich/providers/__init__.py` | Modify | Add `SyncProvider` Protocol + `create_sync_provider` factory |
| `src/core/enrich/providers/gemini_sync.py` | Create | `GeminiSyncProvider` — sync Gemini calls |
| `src/core/enrich/local.py` | Create | `FilterConfig`, `mark_candidates_from_yaml`, `fetch_html_local`, `run_sync_enrich` |
| `src/queries/alert.py` | Create | Alert SQL query |
| `src/cli.py` | Modify | Add `mark-candidates`, `enrich-local`, `alert` subcommands |
| `ai/filters.yaml` | Create | Personal filter config |
| `tests/test_filters.py` | Create | Unit tests for FilterConfig loading |
| `tests/test_enrich_local.py` | Create | Unit tests for mark_candidates SQL and alert query logic |

---

## Task 1: Add `enrich_batch` migration

**Files:**
- Create: `apps/crawler/src/migrations/versions/0004_add_enrich_batch_table.py`

The `enrich_batch` table is referenced by `batch.py`'s `_persist_results` but missing from local migrations.

- [ ] **Step 1: Create the migration file**

```python
# apps/crawler/src/migrations/versions/0004_add_enrich_batch_table.py
"""Add enrich_batch table required by batch.py _persist_results.

Revision ID: 0004
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS enrich_batch (
            id                  text PRIMARY KEY,
            provider            text NOT NULL,
            model               text NOT NULL,
            status              text NOT NULL,
            item_count          integer NOT NULL,
            posting_ids         uuid[] NOT NULL,
            estimated_cost_usd  numeric(10,4),
            input_tokens        integer DEFAULT 0,
            output_tokens       integer DEFAULT 0,
            submitted_at        timestamptz DEFAULT now(),
            completed_at        timestamptz
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_eb_status "
        "ON enrich_batch(status) WHERE status = 'submitted'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS enrich_batch")
```

- [ ] **Step 2: Run the migration**

```bash
cd apps/crawler
uv run alembic upgrade head
```

Expected: `Running upgrade 0003 -> 0004, Add enrich_batch table`

- [ ] **Step 3: Verify**

```bash
uv run python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://crawler:crawler@localhost:5432/crawler')
    row = await conn.fetchrow(\"SELECT column_name FROM information_schema.columns WHERE table_name = 'enrich_batch' ORDER BY ordinal_position LIMIT 1\")
    print(row)
    await conn.close()
asyncio.run(check())
"
```

Expected: row with `column_name = 'id'`

- [ ] **Step 4: Commit**

```bash
git add apps/crawler/src/migrations/versions/0004_add_enrich_batch_table.py
git commit -m "feat: add enrich_batch migration"
```

---

## Task 2: Add config settings

**Files:**
- Modify: `apps/crawler/src/config.py`
- Modify: `apps/crawler/env.local.example`

- [ ] **Step 1: Add settings to `config.py`**

Open `apps/crawler/src/config.py`. In the `Settings` class, after the `enrich_daily_spend_cap_usd` line, add:

```python
    # Local-mode enrichment (personal alert pipeline)
    use_local_descriptions: bool = False   # true = fetch HTML from descriptions table (not R2)
    enrich_mode: str = "batch"             # "batch" | "sync"
    enrich_rate_limit_rpm: int = 15        # Gemini free tier: 15 RPM
    alert_filters_path: str = "ai/filters.yaml"
```

- [ ] **Step 2: Verify settings load**

```bash
cd apps/crawler
uv run python -c "from src.config import settings; print(settings.enrich_rate_limit_rpm)"
```

Expected: `15`

- [ ] **Step 3: Update env.local.example**

Open `apps/crawler/env.local.example`. Add at the end:

```bash
# Local-mode enrichment (personal alert pipeline, phase 1)
USE_LOCAL_DESCRIPTIONS=true
ENRICH_MODE=sync
ENRICH_PROVIDER=gemini
ENRICH_MODEL=gemini-2.0-flash
ENRICH_API_KEY=your_gemini_api_key_here
ENRICH_RATE_LIMIT_RPM=15
```

- [ ] **Step 4: Commit**

```bash
git add apps/crawler/src/config.py apps/crawler/env.local.example
git commit -m "feat: add local-mode enrichment config settings"
```

---

## Task 3: FilterConfig model and YAML loader

**Files:**
- Create: `apps/crawler/src/core/enrich/local.py` (partial — only FilterConfig + load_filter_config)
- Create: `apps/crawler/tests/test_filters.py`

The `FilterConfig` Pydantic model lives in `local.py` (no separate file, per spec).

- [ ] **Step 1: Write the failing tests first**

```python
# apps/crawler/tests/test_filters.py
"""Tests for FilterConfig loading in src/core/enrich/local.py."""

from __future__ import annotations

import textwrap

import pytest
import yaml

from src.core.enrich.local import FilterConfig, OutputConfig, RequireConfig, load_filter_config


def _write_yaml(tmp_path, content):
    p = tmp_path / "filters.yaml"
    p.write_text(textwrap.dedent(content))
    return str(p)


def test_load_valid_config(tmp_path):
    path = _write_yaml(tmp_path, """
        exclude_title_patterns:
          - senior
          - "sr\\\\."
        require:
          work_permit_support: "yes"
          experience_max: 2
        output:
          limit: 50
    """)
    cfg = load_filter_config(path)
    assert cfg.exclude_title_patterns == ["senior", "sr\\."]
    assert cfg.require.work_permit_support == "yes"
    assert cfg.require.experience_max == 2
    assert cfg.output.limit == 50


def test_load_minimal_config(tmp_path):
    path = _write_yaml(tmp_path, """
        require:
          work_permit_support: "yes"
    """)
    cfg = load_filter_config(path)
    assert cfg.exclude_title_patterns == []
    assert cfg.require.experience_max == 2   # default
    assert cfg.output.limit == 100           # default


def test_load_empty_patterns(tmp_path):
    path = _write_yaml(tmp_path, """
        exclude_title_patterns: []
        require:
          work_permit_support: "yes"
    """)
    cfg = load_filter_config(path)
    assert cfg.exclude_title_patterns == []


def test_missing_require_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        exclude_title_patterns:
          - senior
    """)
    with pytest.raises(Exception):
        load_filter_config(path)


def test_invalid_work_permit_value_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        require:
          work_permit_support: "maybe"
    """)
    with pytest.raises(Exception):
        load_filter_config(path)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_filter_config("/nonexistent/path/filters.yaml")


def test_exclude_regex_builds_correctly(tmp_path):
    import re
    path = _write_yaml(tmp_path, """
        exclude_title_patterns:
          - senior
          - "vp\\\\b"
          - "head of"
        require:
          work_permit_support: "yes"
    """)
    cfg = load_filter_config(path)
    regex = "|".join(cfg.exclude_title_patterns)
    assert re.search(regex, "Senior Engineer", re.IGNORECASE)
    assert re.search(regex, "VP of Engineering", re.IGNORECASE)
    assert re.search(regex, "Head of Data", re.IGNORECASE)
    assert not re.search(regex, "Software Engineer", re.IGNORECASE)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/crawler
uv run pytest tests/test_filters.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.core.enrich.local'`

- [ ] **Step 3: Create `local.py` with FilterConfig**

```python
# apps/crawler/src/core/enrich/local.py
"""Local-mode enrichment: filter candidates, sync Gemini enrichment, alert query."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

import asyncpg
import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

log = structlog.get_logger()


# ── Filter config ──────────────────────────────────────────────────────


class RequireConfig(BaseModel):
    work_permit_support: Literal["yes", "no"] | None = "yes"
    experience_max: int | None = 2


class OutputConfig(BaseModel):
    limit: int = 100


class FilterConfig(BaseModel):
    exclude_title_patterns: list[str] = Field(default_factory=list)
    require: RequireConfig
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_filter_config(path: str) -> FilterConfig:
    """Load and validate ai/filters.yaml. Raises FileNotFoundError or ValidationError."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return FilterConfig.model_validate(raw or {})
```

- [ ] **Step 4: Run tests — expect all to pass**

```bash
uv run pytest tests/test_filters.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/crawler/src/core/enrich/local.py apps/crawler/tests/test_filters.py
git commit -m "feat: add FilterConfig model and YAML loader"
```

---

## Task 4: Add `SyncProvider` Protocol to `providers/__init__.py`

**Files:**
- Modify: `apps/crawler/src/core/enrich/providers/__init__.py`

- [ ] **Step 1: Add the Protocol and factory**

Open `apps/crawler/src/core/enrich/providers/__init__.py`. After the existing `BatchProvider` class and `create_provider` factory, append:

```python

class SyncProvider(Protocol):
    """Sync (non-batch) LLM provider for interactive use."""

    async def generate(
        self,
        system_prompt: str,
        user_content: str,
        response_schema: dict,
    ) -> tuple[dict, LLMUsage]:
        """Make one synchronous structured JSON call.

        Returns (parsed_dict, usage).
        """
        ...


def create_sync_provider(provider: str, model: str, api_key: str) -> SyncProvider:
    """Factory for sync providers. Lazily imports the SDK."""
    if provider == "gemini":
        from src.core.enrich.providers.gemini_sync import GeminiSyncProvider

        return GeminiSyncProvider(model=model, api_key=api_key)
    else:
        raise ValueError(f"Unsupported sync provider: {provider!r}. Only 'gemini' supported.")
```

- [ ] **Step 2: Verify import**

```bash
cd apps/crawler
uv run python -c "from src.core.enrich.providers import SyncProvider, create_sync_provider; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/crawler/src/core/enrich/providers/__init__.py
git commit -m "feat: add SyncProvider Protocol and create_sync_provider factory"
```

---

## Task 5: Create `providers/gemini_sync.py`

**Files:**
- Create: `apps/crawler/src/core/enrich/providers/gemini_sync.py`

- [ ] **Step 1: Create the file**

```python
# apps/crawler/src/core/enrich/providers/gemini_sync.py
"""Google Gemini synchronous (non-batch) provider for local-mode enrichment."""

from __future__ import annotations

import json

from src.core.enrich.providers import LLMUsage


class GeminiSyncProvider:
    """Single-call Gemini provider using the aio generate_content API.

    Implements the SyncProvider Protocol:
        async def generate(system_prompt, user_content, response_schema) -> (dict, LLMUsage)
    """

    def __init__(self, model: str, api_key: str) -> None:
        from google import genai  # noqa: PLC0415

        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def generate(
        self,
        system_prompt: str,
        user_content: str,
        response_schema: dict,
    ) -> tuple[dict, LLMUsage]:
        """Make one structured JSON call. Returns (parsed_dict, LLMUsage)."""
        from google.genai import types  # noqa: PLC0415

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )

        text = response.candidates[0].content.parts[0].text
        parsed = json.loads(text)

        um = response.usage_metadata
        usage = LLMUsage(
            input_tokens=um.prompt_token_count or 0 if um else 0,
            output_tokens=um.candidates_token_count or 0 if um else 0,
            model=self._model,
            provider="gemini",
        )
        return parsed, usage
```

- [ ] **Step 2: Verify import**

```bash
cd apps/crawler
uv run python -c "from src.core.enrich.providers.gemini_sync import GeminiSyncProvider; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/crawler/src/core/enrich/providers/gemini_sync.py
git commit -m "feat: add GeminiSyncProvider for local-mode enrichment"
```

---

## Task 6: Create `queries/alert.py`

**Files:**
- Create: `apps/crawler/src/queries/alert.py`

- [ ] **Step 1: Create the file**

```python
# apps/crawler/src/queries/alert.py
"""Alert query: enriched jobs passing visa + experience + title filters."""

from __future__ import annotations

from typing import Any

import asyncpg

_ALERT_QUERY = """
    SELECT
        jp.id,
        jp.titles[1]                            AS title,
        jp.source_url,
        jp.first_seen_at,
        jp.experience_max,
        jp.enrichment->>'work_permit_support'   AS work_permit_support,
        jp.enrichment->>'seniority'             AS seniority,
        jp.enrichment                           AS enrichment_json,
        c.name                                  AS company_name,
        c.slug                                  AS company_slug
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    WHERE jp.is_active = true
      AND jp.enrichment IS NOT NULL
      AND jp.enrichment->>'work_permit_support' = 'yes'
      AND (jp.experience_max IS NULL OR jp.experience_max <= $1)
      AND (jp.titles[1] IS NULL OR jp.titles[1] !~* $2)
    ORDER BY jp.first_seen_at DESC
    LIMIT $3
"""


async def run_alert_query(
    conn: asyncpg.Connection,
    *,
    experience_max: int,
    exclude_title_regex: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return jobs matching all alert filters as plain dicts."""
    rows = await conn.fetch(_ALERT_QUERY, experience_max, exclude_title_regex, limit)
    return [dict(r) for r in rows]
```

- [ ] **Step 2: Verify import**

```bash
cd apps/crawler
uv run python -c "from src.queries.alert import run_alert_query; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/crawler/src/queries/alert.py
git commit -m "feat: add alert query"
```

---

## Task 7: Complete `local.py` — mark_candidates + fetch_html + run_sync_enrich

**Files:**
- Modify: `apps/crawler/src/core/enrich/local.py`
- Create: `apps/crawler/tests/test_enrich_local.py`

- [ ] **Step 1: Write failing tests**

```python
# apps/crawler/tests/test_enrich_local.py
"""Tests for mark_candidates and run_sync_enrich in src/core/enrich/local.py."""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.core.enrich.local import (
    FilterConfig,
    OutputConfig,
    RequireConfig,
    _build_exclude_regex,
    fetch_html_local,
    mark_candidates_from_yaml,
)


# ── _build_exclude_regex ──────────────────────────────────────────────


def test_build_exclude_regex_joins_patterns():
    import re
    regex = _build_exclude_regex(["senior", "lead", "director"])
    assert re.search(regex, "Senior Engineer", re.IGNORECASE)
    assert re.search(regex, "Tech Lead", re.IGNORECASE)
    assert not re.search(regex, "Software Engineer", re.IGNORECASE)


def test_build_exclude_regex_empty_returns_no_match_pattern():
    import re
    regex = _build_exclude_regex([])
    # Empty pattern should match nothing
    assert not re.search(regex, "Senior Engineer", re.IGNORECASE)
    assert not re.search(regex, "any job title", re.IGNORECASE)


# ── mark_candidates_from_yaml ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_candidates_calls_two_updates(tmp_path):
    """mark_candidates runs a reset UPDATE then a filter UPDATE."""
    config_path = tmp_path / "filters.yaml"
    config_path.write_text(textwrap.dedent("""
        exclude_title_patterns:
          - senior
        require:
          work_permit_support: "yes"
          experience_max: 2
    """))

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=["UPDATE 100", "UPDATE 30"])

    result = await mark_candidates_from_yaml(pool, str(config_path))

    assert pool.execute.call_count == 2
    assert result["marked"] == 100
    assert result["cleared"] == 30


@pytest.mark.asyncio
async def test_mark_candidates_uses_experience_max_from_config(tmp_path):
    config_path = tmp_path / "filters.yaml"
    config_path.write_text(textwrap.dedent("""
        exclude_title_patterns: []
        require:
          work_permit_support: "yes"
          experience_max: 3
    """))

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=["UPDATE 50", "UPDATE 5"])

    await mark_candidates_from_yaml(pool, str(config_path))

    # Second call (filter UPDATE) should have experience_max=3 as parameter
    second_call_args = pool.execute.call_args_list[1]
    assert 3 in second_call_args[0]  # experience_max=3 in positional args


# ── fetch_html_local ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_html_local_returns_html():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value="<p>Job description</p>")

    html = await fetch_html_local(pool, "some-uuid", "en")
    assert html == "<p>Job description</p>"


@pytest.mark.asyncio
async def test_fetch_html_local_returns_none_when_missing():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)

    html = await fetch_html_local(pool, "some-uuid", "en")
    assert html is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/crawler
uv run pytest tests/test_enrich_local.py -v
```

Expected: failures with `ImportError: cannot import name '_build_exclude_regex'`

- [ ] **Step 3: Append the remaining functions to `local.py`**

Open `apps/crawler/src/core/enrich/local.py` and append after `load_filter_config`:

```python

# ── Helpers ────────────────────────────────────────────────────────────


def _build_exclude_regex(patterns: list[str]) -> str:
    """Build a case-insensitive regex alternation from a list of patterns.

    Returns '(?!)' (matches nothing) when patterns is empty so SQL !~* is safe.
    """
    if not patterns:
        return "(?!)"
    return "|".join(patterns)


# ── Claim query (local mode — no R2 requirement) ───────────────────────

_CLAIM_PENDING_LOCAL = """
UPDATE job_posting
SET to_be_enriched = false
WHERE id IN (
    SELECT id FROM job_posting
    WHERE is_active = true
      AND to_be_enriched = true
      AND enrichment IS NULL
    ORDER BY first_seen_at DESC
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
RETURNING id,
          titles[1]      AS title,
          locales[1]     AS locale,
          employment_type
"""


# ── mark-candidates ────────────────────────────────────────────────────


async def mark_candidates_from_yaml(pool: asyncpg.Pool, yaml_path: str) -> dict:
    """Flag postings that pass cheap filters as to_be_enriched=true.

    Step 1 — Reset all unenriched active postings to candidates.
    Step 2 — Clear the ones that fail title regex or experience cap.

    Returns {"marked": N, "cleared": M}.
    """
    config = load_filter_config(yaml_path)
    exclude_regex = _build_exclude_regex(config.exclude_title_patterns)
    experience_max = config.require.experience_max if config.require.experience_max is not None else 9999

    # Step 1: Reset (idempotent)
    reset_result = await pool.execute(
        "UPDATE job_posting SET to_be_enriched = true "
        "WHERE is_active = true AND enrichment IS NULL"
    )
    marked_count = int(reset_result.split()[-1])

    # Step 2: Clear those that fail cheap filters
    cleared_result = await pool.execute(
        """
        UPDATE job_posting
        SET to_be_enriched = false
        WHERE is_active = true
          AND enrichment IS NULL
          AND (
            (titles[1] IS NOT NULL AND titles[1] ~* $1)
            OR (experience_max IS NOT NULL AND experience_max > $2)
          )
        """,
        exclude_regex,
        experience_max,
    )
    cleared_count = int(cleared_result.split()[-1])

    log.info(
        "mark_candidates.done",
        marked=marked_count,
        cleared=cleared_count,
        exclude_regex=exclude_regex,
        experience_max=experience_max,
    )
    return {"marked": marked_count, "cleared": cleared_count}


# ── fetch HTML from local descriptions table ───────────────────────────


async def fetch_html_local(pool: asyncpg.Pool, posting_id: str, locale: str) -> str | None:
    """Fetch HTML from the local descriptions table."""
    return await pool.fetchval(
        "SELECT html FROM descriptions WHERE posting_id = $1::uuid AND locale = $2 LIMIT 1",
        posting_id,
        locale,
    )


# ── sync enrichment loop ───────────────────────────────────────────────


async def run_sync_enrich(
    pool: asyncpg.Pool,
    provider,
    *,
    batch_size: int = 20,
    rate_limit_rpm: int = 15,
) -> dict:
    """Claim pending postings, enrich via sync Gemini calls, persist results.

    provider — SyncProvider instance (GeminiSyncProvider).
    batch_size — postings per claim iteration (default 20).
    rate_limit_rpm — max Gemini calls per minute (default 15).

    Returns {"enriched": N, "failed": M, "skipped": K}.
    """
    from src.config import settings
    from src.core.enrich.batch import _persist_results
    from src.core.enrich.job import ENRICH_VERSION, SYSTEM_PROMPT, EnrichmentResult, build_user_message

    total_enriched = total_failed = total_skipped = 0

    while True:
        rows = await pool.fetch(_CLAIM_PENDING_LOCAL, batch_size)
        if not rows:
            break

        results: list[tuple[str, dict | None, object | None]] = []
        posting_ids: list[str] = []

        for i, row in enumerate(rows):
            pid = str(row["id"])
            posting_ids.append(pid)
            locale = row["locale"] or "en"

            html = await fetch_html_local(pool, pid, locale)
            if not html:
                log.warning("enrich.local.no_html", posting_id=pid, locale=locale)
                # Re-queue so it can be retried after description is populated
                await pool.execute(
                    "UPDATE job_posting SET to_be_enriched = true WHERE id = $1::uuid",
                    pid,
                )
                results.append((pid, None, None))
                total_skipped += 1
                continue

            # Rate-limit: sleep between calls (not before the first)
            if i > 0:
                await asyncio.sleep(60 / rate_limit_rpm)

            user_msg = build_user_message(
                html,
                title=row["title"],
                locations=None,  # local mode has no denormalized text locations
                employment_type=row["employment_type"],
            )

            try:
                parsed_dict, usage = await provider.generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_content=user_msg,
                    response_schema=EnrichmentResult.model_json_schema(),
                )
                log.info("enrich.local.gemini_call", posting_id=pid)
                results.append((pid, parsed_dict, usage))
                total_enriched += 1
            except Exception as exc:
                log.warning("enrich.local.gemini_error", posting_id=pid, error=str(exc))
                # Re-queue for retry
                await pool.execute(
                    "UPDATE job_posting SET to_be_enriched = true WHERE id = $1::uuid",
                    pid,
                )
                results.append((pid, None, None))
                total_failed += 1

        if not results:
            continue

        # Insert synthetic enrich_batch row before calling _persist_results
        # (_persist_results does UPDATE enrich_batch SET status='completed' at the end)
        batch_id = f"local_sync_{uuid4()}"
        await pool.execute(
            """
            INSERT INTO enrich_batch (id, provider, model, status, item_count, posting_ids)
            VALUES ($1, 'gemini', $2, 'submitted', $3, $4::uuid[])
            """,
            batch_id,
            settings.enrich_model or "gemini-2.0-flash",
            len(posting_ids),
            posting_ids,
        )

        await _persist_results(pool, results, batch_id)

        log.info(
            "enrich.local.batch_done",
            batch_id=batch_id,
            enriched=total_enriched,
            failed=total_failed,
            skipped=total_skipped,
        )

    return {"enriched": total_enriched, "failed": total_failed, "skipped": total_skipped}
```

- [ ] **Step 4: Run tests — expect all to pass**

```bash
uv run pytest tests/test_enrich_local.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/crawler/src/core/enrich/local.py apps/crawler/tests/test_enrich_local.py
git commit -m "feat: add mark_candidates, fetch_html_local, run_sync_enrich"
```

---

## Task 8: Add CLI commands to `cli.py`

**Files:**
- Modify: `apps/crawler/src/cli.py`

The crawler CLI uses `argparse` (not Click). The pattern is: add subparsers in `parse_args()`, dispatch with `elif args.command == "..."` in `run()`.

- [ ] **Step 1: Add subparsers in `parse_args()`**

Open `apps/crawler/src/cli.py`. In `parse_args()`, find the last `sub.add_parser(...)` call (the `board` subparser). After it, before `return parser.parse_args()`, add:

```python
    # Phase 1: local alert pipeline
    mark_p = sub.add_parser(
        "mark-candidates",
        help="Flag postings that pass cheap filters as enrichment candidates",
    )
    mark_p.add_argument(
        "--filters",
        default="ai/filters.yaml",
        help="Path to filters YAML (default: ai/filters.yaml)",
    )

    enrich_local_p = sub.add_parser(
        "enrich-local",
        help="Enrich flagged postings via sync Gemini calls (local mode)",
    )
    enrich_local_p.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Postings per claim iteration (default: 20)",
    )
    enrich_local_p.add_argument(
        "--rate-limit-rpm",
        type=int,
        default=None,
        help="Gemini calls per minute (default: from ENRICH_RATE_LIMIT_RPM env, fallback 15)",
    )

    alert_p = sub.add_parser(
        "alert",
        help="Print visa-sponsoring entry-level jobs as JSON",
    )
    alert_p.add_argument(
        "--filters",
        default="ai/filters.yaml",
        help="Path to filters YAML (default: ai/filters.yaml)",
    )
    alert_p.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help="Output format (default: json)",
    )
```

- [ ] **Step 2: Add command handlers in `run()`**

In `run()`, find the last `elif args.command == "board":` block. After its closing code (before `finally:`), add:

```python
        elif args.command == "mark-candidates":
            local_pool = await create_local_pool()
            from src.core.enrich.local import mark_candidates_from_yaml

            result = await mark_candidates_from_yaml(local_pool, args.filters)
            print(
                f"mark-candidates: {result['marked']} candidates flagged, "
                f"{result['cleared']} cleared"
            )

        elif args.command == "enrich-local":
            local_pool = await create_local_pool()
            from src.core.enrich.local import run_sync_enrich
            from src.core.enrich.providers import create_sync_provider

            rpm = args.rate_limit_rpm or settings.enrich_rate_limit_rpm
            provider = create_sync_provider(
                settings.enrich_provider or "gemini",
                settings.enrich_model or "gemini-2.0-flash",
                settings.enrich_api_key,
            )
            result = await run_sync_enrich(
                local_pool,
                provider,
                batch_size=args.batch_size,
                rate_limit_rpm=rpm,
            )
            print(
                f"enrich-local: enriched={result['enriched']} "
                f"failed={result['failed']} skipped={result['skipped']}"
            )

        elif args.command == "alert":
            import json as _json

            local_pool = await create_local_pool()
            from src.core.enrich.local import _build_exclude_regex, load_filter_config
            from src.queries.alert import run_alert_query

            cfg = load_filter_config(args.filters)
            exclude_regex = _build_exclude_regex(cfg.exclude_title_patterns)
            experience_max = cfg.require.experience_max if cfg.require.experience_max is not None else 9999

            async with local_pool.acquire() as conn:
                rows = await run_alert_query(
                    conn,
                    experience_max=experience_max,
                    exclude_title_regex=exclude_regex,
                    limit=cfg.output.limit,
                )

            log.info("alert.query", row_count=len(rows))

            if args.format == "json":
                # Convert non-serializable types
                output = []
                for r in rows:
                    d = dict(r)
                    for k, v in d.items():
                        if hasattr(v, "isoformat"):
                            d[k] = v.isoformat()
                    output.append(d)
                print(_json.dumps(output, indent=2, ensure_ascii=False))
            else:
                # table format
                if not rows:
                    print("No matching jobs.")
                else:
                    print(f"{'Title':<50} {'Company':<30} {'Score':<8} {'First seen'}")
                    print("-" * 100)
                    for r in rows:
                        print(
                            f"{str(r.get('title') or '')[:49]:<50} "
                            f"{str(r.get('company_name') or '')[:29]:<30} "
                            f"{str(r.get('work_permit_support') or ''):<8} "
                            f"{str(r.get('first_seen_at') or '')[:10]}"
                        )
```

- [ ] **Step 3: Smoke-test all three subparsers**

```bash
cd apps/crawler
uv run crawler mark-candidates --help
uv run crawler enrich-local --help
uv run crawler alert --help
```

Expected: each shows usage with their respective flags.

- [ ] **Step 4: Commit**

```bash
git add apps/crawler/src/cli.py
git commit -m "feat: add mark-candidates, enrich-local, alert CLI commands"
```

---

## Task 9: Create `ai/filters.yaml`

**Files:**
- Create: `ai/filters.yaml`

- [ ] **Step 1: Create the file**

```yaml
# ai/filters.yaml — personal job alert filters (phase 1)
# Run: crawler mark-candidates && crawler enrich-local && crawler alert

# Title-keyword exclusions — case-insensitive regex fragments joined with |
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
  # "yes" = job explicitly offers visa/work-permit sponsorship.
  # null or "no" values are excluded.
  work_permit_support: "yes"

  # Max years of experience listed in the posting.
  # NULL in DB (no requirement stated) is also accepted.
  experience_max: 2

output:
  limit: 100
```

- [ ] **Step 2: Verify FilterConfig accepts this file**

```bash
cd apps/crawler
uv run python -c "
from src.core.enrich.local import load_filter_config
cfg = load_filter_config('../../ai/filters.yaml')
print('patterns:', len(cfg.exclude_title_patterns))
print('experience_max:', cfg.require.experience_max)
print('limit:', cfg.output.limit)
"
```

Expected:
```
patterns: 9
experience_max: 2
limit: 100
```

- [ ] **Step 3: Commit**

```bash
git add ai/filters.yaml
git commit -m "feat: add ai/filters.yaml personal job filters"
```

---

## Task 10: End-to-end smoke test

No new files. Verifies all three commands run against the real local Postgres (5,275 postings).

- [ ] **Step 1: Run mark-candidates**

```bash
cd apps/crawler
uv run crawler mark-candidates --filters ../../ai/filters.yaml
```

Expected: `mark-candidates: N candidates flagged, M cleared` where N + M ≈ 5,275

- [ ] **Step 2: Verify flags in Postgres**

```bash
uv run python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://crawler:crawler@localhost:5432/crawler')
    flagged = await conn.fetchval('SELECT count(*) FROM job_posting WHERE to_be_enriched = true AND is_active = true')
    cleared = await conn.fetchval('SELECT count(*) FROM job_posting WHERE to_be_enriched = false AND enrichment IS NULL AND is_active = true')
    print(f'flagged={flagged}, cleared={cleared}')
    await conn.close()
asyncio.run(check())
"
```

Expected: `flagged=N, cleared=M` with N + M matching the mark-candidates output.

- [ ] **Step 3: Enrich a small batch (real Gemini call)**

```bash
ENRICH_PROVIDER=gemini ENRICH_MODEL=gemini-2.0-flash ENRICH_API_KEY=<your_key> \
  uv run crawler enrich-local --batch-size 3 --rate-limit-rpm 15
```

Expected: `enrich-local: enriched=3 failed=0 skipped=0` (or similar)

- [ ] **Step 4: Verify enrichment written to Postgres**

```bash
uv run python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://crawler:crawler@localhost:5432/crawler')
    row = await conn.fetchrow(\"SELECT titles[1], enrichment->>'work_permit_support' AS wps, enrichment->>'seniority' AS sen FROM job_posting WHERE enrichment IS NOT NULL LIMIT 1\")
    print(dict(row))
    await conn.close()
asyncio.run(check())
"
```

Expected: row with `wps` as `'yes'`, `'no'`, or `None`.

- [ ] **Step 5: Run alert command**

```bash
uv run crawler alert --filters ../../ai/filters.yaml --format table
```

Expected: either `No matching jobs.` (if no enriched jobs have `work_permit_support='yes'` yet) or a table of matching jobs.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/test_filters.py tests/test_enrich_local.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Final commit**

```bash
git add -u
git commit -m "feat: Phase 1 local job alert filter complete"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Implemented in |
|---|---|
| `crawler mark-candidates` command | Task 8, `mark_candidates_from_yaml` (Task 7) |
| Title regex exclusion (cheap filter) | Task 7 `_build_exclude_regex`, SQL in `mark_candidates_from_yaml` |
| Experience cap filter | Task 7 `mark_candidates_from_yaml` SQL |
| Idempotent mark-candidates | Task 7 (reset step 1, then filter step 2) |
| `crawler enrich-local` command | Task 8, `run_sync_enrich` (Task 7) |
| Claim from local descriptions table | Task 7 `_CLAIM_PENDING_LOCAL` (no R2 check), `fetch_html_local` |
| Sync Gemini calls, rate-limited | Task 7 `run_sync_enrich` (`asyncio.sleep(60/rpm)`) |
| Reuse `_persist_results` from batch.py | Task 7 `run_sync_enrich` imports and calls it |
| Synthetic `enrich_batch` row | Task 7 (INSERT before `_persist_results`) |
| `crawler alert` command | Task 8, `run_alert_query` (Task 6) |
| work_permit_support='yes' filter | Task 6 `_ALERT_QUERY` |
| JSON output to stdout | Task 8 `alert` handler |
| `ai/filters.yaml` | Task 9 |
| `FilterConfig` Pydantic model | Task 3 |
| `SyncProvider` Protocol | Task 4 |
| `GeminiSyncProvider` | Task 5 |
| `enrich_batch` migration | Task 1 |
| Config settings (4 new) | Task 2 |
| Tests for FilterConfig | Task 3 |
| Tests for mark_candidates + fetch_html | Task 7 |

**Placeholder scan:** No TBD, TODO, "implement later" found. All SQL, code, and commands shown in full.

**Type consistency:**
- `_build_exclude_regex` defined in Task 7, used in Task 7 (mark_candidates) and Task 8 (alert handler) — consistent.
- `run_alert_query(conn, *, experience_max, exclude_title_regex, limit)` defined in Task 6, called in Task 8 — matches exactly.
- `SyncProvider.generate(system_prompt, user_content, response_schema) -> tuple[dict, LLMUsage]` defined in Task 4, implemented in Task 5, called in Task 7 — consistent.
- `mark_candidates_from_yaml(pool, yaml_path) -> dict` defined in Task 7, called in Task 8 — consistent.
- `run_sync_enrich(pool, provider, *, batch_size, rate_limit_rpm) -> dict` defined in Task 7, called in Task 8 — consistent.
