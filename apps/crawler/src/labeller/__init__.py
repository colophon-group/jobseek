"""Daily labelled-postings routine.

A Claude Code-driven pipeline that samples job postings, runs a deterministic
HTML normalizer, invokes specialized Sonnet subagents to produce structured
labels, canonicalizes free-text labels against the crawler taxonomies, and
publishes a gold dataset to HuggingFace.

Entry point: ``labeller = "src.labeller.cli:main"`` in pyproject.toml.
Orchestrator prompt: ``.claude/commands/jobseek-label-daily.md``.
Subagent definitions: ``.claude/agents/jobseek-labeller-*.md``.
Design doc: ``docs/15-data-sampling-routine.md``.
"""

from __future__ import annotations

__version__ = "0.1.0"
