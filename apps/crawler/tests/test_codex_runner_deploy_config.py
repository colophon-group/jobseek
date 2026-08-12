"""Behavioral shell tests for Codex runner runtime-config deployment gates."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "scripts" / "deploy-codex-runner-host.sh"
LEAK_MARKER = "not-a-real-password-7f93"

_REQUIRE_CONFIG = r"""
set -euo pipefail
export JOBSEEK_CODEX_CONFIG_DIR="$1"
source "$2"
as_runner() { "$@"; }
require_runtime_config
"""


def _validate(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "governor.env").write_text("# test governor config\n", encoding="utf-8")
    (config_dir / "labeller.env").write_text(content, encoding="utf-8")
    return subprocess.run(
        ["bash", "-c", _REQUIRE_CONFIG, "bash", str(config_dir), str(DEPLOY)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "content",
    [
        f"LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n",
        f"# DSN only\n\nLOCAL_DATABASE_URL='postgresql://reader:{LEAK_MARKER}@db/crawler'\n",
        f'  # comment\nLOCAL_DATABASE_URL="postgres://reader:{LEAK_MARKER}@db/crawler"\n',
    ],
)
def test_require_runtime_config_accepts_one_nonempty_postgresql_dsn(
    tmp_path: Path, content: str
) -> None:
    result = _validate(tmp_path, content)

    assert result.returncode == 0, result.stderr
    assert LEAK_MARKER not in result.stdout
    assert LEAK_MARKER not in result.stderr


@pytest.mark.parametrize(
    "content",
    [
        "",
        "# no assignment\n",
        "LOCAL_DATABASE_URL=\n",
        "LOCAL_DATABASE_URL=   \n",
        "LOCAL_DATABASE_URL=''\n",
        'LOCAL_DATABASE_URL="   "\n',
        f'LOCAL_DATABASE_URL="postgresql://reader:{LEAK_MARKER}@db/crawler\n',
        "LOCAL_DATABASE_URL=https://db.invalid/crawler\n",
        "LOCAL_DATABASE_URL=postgresql://\n",
        "LOCAL_DATABASE_URL=postgresql://db name/crawler\n",
        "OTHER_KEY=value\n",
        f"export LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n",
        f" LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n",
        (
            f"LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n"
            "CRAWLER_DB_POOL_MAX=2\n"
        ),
        (
            f"LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n"
            "LOCAL_DATABASE_URL=postgresql://reader:second-password@db/crawler\n"
        ),
    ],
)
def test_require_runtime_config_rejects_empty_duplicate_or_extra_assignments(
    tmp_path: Path, content: str
) -> None:
    result = _validate(tmp_path, content)

    assert result.returncode != 0
    assert LEAK_MARKER not in result.stdout
    assert LEAK_MARKER not in result.stderr
    assert "invalid labeller.env" in result.stderr
