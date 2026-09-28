#!/usr/bin/env python3
"""Validate repository-owned agent inputs without model calls or personal paths.

Run with the crawler's frozen environment. These checks are required even for
Markdown-only PRs; behavioral renderer/state tests run alongside this script.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
INSTRUCTION_BUDGET = 24 * 1024  # Reserve 8 KiB of the Codex default for user guidance.


def check(root: Path = ROOT) -> dict:
    instruction_paths = [
        root / "AGENTS.md",
        *sorted((root / "apps").glob("*/AGENTS.md")),
    ]
    for path in instruction_paths:
        chain = [path] if path == root / "AGENTS.md" else [root / "AGENTS.md", path]
        size = sum(item.stat().st_size for item in chain)
        if size > INSTRUCTION_BUDGET:
            raise ValueError(f"{path.relative_to(root)} instruction chain exceeds 24 KiB: {size}")

    for path in sorted((root / ".codex/agents").glob("*.toml")):
        data = tomllib.loads(path.read_text())
        for field in ("name", "description", "developer_instructions"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise ValueError(f"{path}: missing {field}")
        if data["name"] != path.stem:
            raise ValueError(f"{path}: name does not match filename")
        for ref in re.findall(r"`(\.agents/[^`]+)`", data["developer_instructions"]):
            if not (root / ref).is_file():
                raise ValueError(f"{path}: missing shared contract {ref}")

    for path in sorted((root / ".agents/skills").glob("*/SKILL.md")):
        parts = path.read_text().split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError(f"{path}: missing YAML frontmatter")
        data = yaml.safe_load(parts[1])
        if not isinstance(data, dict) or not all(data.get(k) for k in ("name", "description")):
            raise ValueError(f"{path}: missing skill name/description")
        if re.search(r"/(Users|home)/[^/\s]+/\.codex/", path.read_text()):
            raise ValueError(f"{path}: personal Codex dependency")

    inputs = set(instruction_paths)
    for pattern in (
        ".agents/**/*.md",
        ".codex/agents/*.toml",
        "apps/crawler/src/workspace/steps/**/*.md",
        "apps/crawler/src/labeller/prompts/**/*.j2",
    ):
        inputs.update(root.glob(pattern))
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    for path in sorted(inputs):
        if path.suffix in (".md", ".j2") and (
            "/steps/parallel/" in str(path) or path.suffix == ".j2"
        ):
            env.parse(path.read_text())
    digest = hashlib.sha256()
    for path in sorted(inputs):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return {
        "files": len(inputs),
        "instruction_sha256": digest.hexdigest(),
        "codex_version": (root / "deploy/codex-version").read_text().strip(),
    }


if __name__ == "__main__":
    print(json.dumps(check(), sort_keys=True))
