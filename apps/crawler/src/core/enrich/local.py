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
