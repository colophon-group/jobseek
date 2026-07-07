"""Daily labelled-postings routine.

A Codex-first agent pipeline that samples job postings, prepares deterministic
task inputs, invokes task-specific subagents for normalization, section
splitting, and structured extraction, validates the outputs, and publishes
accepted gold rows to HuggingFace.

Entry point: ``labeller = "src.labeller.cli:main"`` in pyproject.toml.
Codex skill: ``.agents/skills/jobseek-label-daily/SKILL.md``.
Subagent contracts: ``.agents/labeller/*.md``.
Design doc: ``docs/15-data-sampling-routine.md``.
"""

from __future__ import annotations

__version__ = "0.1.0"
