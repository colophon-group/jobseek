from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"


@pytest.mark.parametrize(
    ("backend", "inspect_status", "apply_status", "expected_calls", "expected_status"),
    [("go", 0, 0, 2, 0), ("go", 23, 0, 1, 23), ("go", 0, 27, 2, 27), ("legacy", 0, 0, 0, 0)],
)
def test_missing_schedule_repair_preserves_authority_and_stops_on_failure(
    tmp_path: Path,
    backend: str,
    inspect_status: int,
    apply_status: int,
    expected_calls: int,
    expected_status: int,
) -> None:
    source = DEPLOY_SH.read_text()
    start = source.index("repair_default_missing_schedules() {")
    function = source[start : source.index("\n}\n", start) + 3]
    log = tmp_path / "calls.log"
    harness = "\n".join(
        (
            "set -u",
            'CRAWLER_RUNTIME_BACKEND="$TEST_BACKEND"',
            'CRAWLER_IMAGE_REF="ghcr.io/colophon-group/jobseek-crawler@sha256:' + "a" * 64 + '"',
            "MAINTENANCE_PROVENANCE_LABELS=(--label provenance=qualified)",
            """docker() { printf '%s\\n' "$*" >>"$TEST_LOG"; """
            'if [[ "$*" == *--apply* ]]; then return "$APPLY_STATUS"; '
            'else return "$INSPECT_STATUS"; fi; }',
            function,
            "if repair_default_missing_schedules; then status=0; else status=$?; fi",
            """printf 'status=%s\\n' "$status" >>"$TEST_LOG" """,
        )
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TEST_LOG": str(log),
            "TEST_BACKEND": backend,
            "INSPECT_STATUS": str(inspect_status),
            "APPLY_STATUS": str(apply_status),
        },
    )
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines[-1] == f"status={expected_status}"
    assert len(lines) == expected_calls + 1
    for index, call in enumerate(lines[:-1]):
        assert "-e LOCAL_DATABASE_URL -e REDIS_URL" in call
        assert "-e CRAWLER_DB_POOL_MIN=0 -e CRAWLER_DB_POOL_MAX=1" in call
        assert "jobseek-lightpanda-b0-producer-socket:/run/jobseek-lightpanda-producer:ro" in call
        assert "--label provenance=qualified" in call
        assert "go-typesense-exporter --redis-capacity repair-missing --limit 50000" in call
        assert ("--apply" in call) == (index == 1)


def test_missing_schedule_repair_runs_quiesced_after_epoch_before_restart() -> None:
    source = DEPLOY_SH.read_text()
    quiesce = source.index("docker compose stop --timeout 60")
    sync = source.index("go-typesense-exporter --sync-registry", quiesce)
    epoch = source.index("\nprepare_default_go_epoch\n", sync)
    repair = source.index("\nrepair_default_missing_schedules\n", epoch)
    restart = source.index('docker compose up -d "${CRAWLER_STACK_SERVICES[@]}"', repair)
    assert quiesce < sync < epoch < repair < restart
