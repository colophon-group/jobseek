"""Explicit project role registration for noninteractive Codex invocations.

Standalone role files are canonical. Passing their registry as CLI overrides
also works when a new worktree has no persisted project-trust configuration.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


def project_agent_overrides(root: Path) -> list[str]:
    args: list[str] = []
    for path in sorted((root / ".codex/agents").glob("*.toml")):
        role = tomllib.loads(path.read_text())
        name = role["name"]
        if name != path.stem or not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError(f"Invalid project agent name: {path.name}")
        for key, value in (
            ("description", role["description"]),
            ("config_file", str(path.resolve())),
        ):
            args.extend(("--config", f"agents.{name}.{key}={json.dumps(value)}"))
    return args
