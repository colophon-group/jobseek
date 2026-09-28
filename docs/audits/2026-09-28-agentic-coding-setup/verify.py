"""Offline audit reproductions; run with the crawler's frozen Python environment.

All mutations use temporary directories. GitHub and Git commands in shell
probes are stubs. This never launches Codex, contacts production, or merges a PR.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/crawler"))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


def executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o700)


def instruction_budget() -> dict:
    root = (ROOT / "AGENTS.md").read_bytes()
    crawler = (ROOT / "apps/crawler/AGENTS.md").read_bytes()
    remaining = max(0, 32768 - len(root))
    return {
        "root_bytes": len(root),
        "crawler_bytes": len(crawler),
        "combined_bytes": len(root) + len(crawler),
        "default_budget_bytes": 32768,
        "crawler_cutoff_line_upper_bound": crawler[:remaining].count(b"\n") + 1,
        "caveat": "Excludes global instructions and separators; not a live Codex session capture.",
    }


def classify_prompts() -> dict:
    paths = [
        "AGENTS.md",
        "apps/crawler/AGENTS.md",
        ".agents/skills/jobseek-label-daily/SKILL.md",
        ".agents/labeller/extractor.md",
        "apps/crawler/src/workspace/steps/parallel/config-tester.md",
        "docs/18-codex-automation-deployment.md",
        ".codex/agents/jobseek-config-tester.toml",
    ]
    results = {}
    with tempfile.TemporaryDirectory(prefix="jobseek-audit-classify-") as tmp:
        directory = Path(tmp)
        executable(
            directory / "gh",
            '#!/bin/sh\ncase "$*" in\n'
            '*--paginate*) printf "%s\\n" "$AUDIT_PATH";;\n'
            '*) printf "main\\n";;\nesac\n',
        )
        for path in paths:
            env = {
                "PATH": f"{directory}:/usr/bin:/bin",
                "GH_TOKEN": "audit-stub",
                "REPO": "audit/repo",
                "PR": "1",
                "AUDIT_PATH": path,
            }
            result = subprocess.run(
                ["bash", str(ROOT / ".github/scripts/classify-pr-paths.sh")],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            results[path] = dict(line.split("=", 1) for line in result.stdout.splitlines())
    return results


def board_interleaving() -> dict:
    from click.testing import CliRunner

    from src.workspace import state
    from src.workspace.board_claim_kv import BoardBackedClaimKV
    from src.workspace.commands import crawl
    from src.workspace.lib.select import select_monitor

    with (
        tempfile.TemporaryDirectory(prefix="jobseek-audit-state-") as tmp,
        patch.object(state, "get_workspace_dir", return_value=Path(tmp)),
    ):
        state.save_board(
            "audit",
            state.Board(alias="careers", slug="audit-careers", url="https://example.invalid"),
        )
        first = state.load_board("audit", "careers")
        second = state.load_board("audit", "careers")
        first.configs["agent-a"] = {"monitor_type": "dom"}
        second.configs["agent-b"] = {"monitor_type": "sitemap"}
        state.save_board("audit", first)
        state.save_board("audit", second)
        remaining = sorted(state.load_board("audit", "careers").configs)

        board = state.Board(alias="careers", slug="audit-careers", url="https://example.invalid")
        asyncio.run(select_monitor(BoardBackedClaimKV(board), "dom", "agent-a"))
        asyncio.run(select_monitor(BoardBackedClaimKV(board), "sitemap", "agent-b"))
        # Only workspace resolution is replaced; run the real scraper
        # selection command, with A's turn resuming after B selected.
        with patch.object(
            crawl,
            "_resolve_board",
            return_value=(state.Workspace(slug="audit"), board),
        ):
            result = CliRunner().invoke(
                crawl.select_scraper, ["audit", "json-ld", "--board", "careers"]
            )
        if result.exception:
            raise result.exception
        selections = {name: config.get("scraper_type") for name, config in board.configs.items()}
    assert remaining == ["agent-b"], remaining
    assert selections == {"agent-a": None, "agent-b": "json-ld"}, selections
    return {
        "after_two_stale_board_saves": remaining,
        "scraper_selected_by_resuming_agent_a": selections,
        "cli_exit_code": result.exit_code,
    }


def merge_interleaving() -> dict:
    with tempfile.TemporaryDirectory(prefix="jobseek-audit-merge-") as tmp:
        directory = Path(tmp)
        calls = directory / "calls"
        executable(
            directory / "git",
            '#!/bin/sh\nprintf "git %s\\n" "$*" >> "$AUDIT_CALLS"\nexit 0\n',
        )
        executable(
            directory / "gh",
            r"""#!/bin/sh
printf 'gh %s\n' "$*" >> "$AUDIT_CALLS"
case "$*" in
  *statusCheckRollup*)
    printf 'simulated head advanced from A to B before CI success\n' >> "$AUDIT_CALLS"
    printf 'SUCCESS\n';;
  *headRepositoryOwner*)
    printf '%s%s\n' '{"state":"OPEN","isDraft":false,"headRefName":"add-company/audit",' \
      '"headRepositoryOwner":{"login":"audit"}}';;
  *pr\ merge*) printf 'simulated merge attempt on current head B\n' >> "$AUDIT_CALLS";;
  *--paginate*) printf 'apps/crawler/data/boards.csv\n';;
  *) exit 3;;
esac
""",
        )
        executable(
            directory / "label-pr.sh",
            '#!/bin/sh\nprintf "classified head A as config-only\\n" >> "$AUDIT_CALLS"\n'
            'printf "labels=auto-merge\\n" >> "$GITHUB_OUTPUT"\n',
        )
        for name in (
            "dispatch-pr-checks.sh",
            "dispatch-company-production-sync.sh",
            "close-linked-company-request-issues.sh",
        ):
            executable(directory / name, "#!/bin/sh\nexit 0\n")
        env = {
            "PATH": f"{directory}:" + os.environ["PATH"],
            "GH_TOKEN": "audit-stub",
            "REPO": "audit/repo",
            "PR": "1",
            "TRUSTED_SCRIPTS_DIR": str(directory),
            "AUDIT_CALLS": str(calls),
        }
        result = subprocess.run(
            ["bash", str(ROOT / ".github/scripts/maybe-auto-merge-pr.sh")],
            cwd=directory,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        log = calls.read_text().splitlines()
    merge = [line for line in log if line.startswith("gh pr merge ")]
    assert len(merge) == 1 and "--match-head-commit" not in merge[0], log
    assert log.count("classified head A as config-only") == 1, log
    return {
        "calls": log,
        "stdout": result.stdout.splitlines(),
        "caveat": "Mocked GitHub/Git; demonstrates an unbound merge request, not a live merge.",
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "instruction_budget": instruction_budget(),
                "ci_classification": classify_prompts(),
                "parallel_board_state": board_interleaving(),
                "merge_interleaving": merge_interleaving(),
            },
            indent=2,
        )
    )
