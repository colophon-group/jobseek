"""Safety contracts for the ordinary Hetzner ATS inventory runner."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "ats-inventory"
RUNNER = DEPLOY / "run.sh"
CONTROL = DEPLOY / "control.sh"
INSTALLER = DEPLOY / "install-host.sh"
RECEIVER = DEPLOY / "install-host-from-stdin.sh"
REMOTE = DEPLOY / "deploy-remote.sh"
TOKEN_HELPER = DEPLOY / "github-app-token.py"
STATUS_HELPER = DEPLOY / "status.py"
BOUNDED_TEE = DEPLOY / "bounded-tee.py"
SERVICE = ROOT / "deploy" / "systemd" / "jobseek-ats-inventory.service"
TIMER = ROOT / "deploy" / "systemd" / "jobseek-ats-inventory.timer"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-ats-inventory.yml"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


token_helper = _load("ats_inventory_github_app_token", TOKEN_HELPER)
status_helper = _load("ats_inventory_status", STATUS_HELPER)
bounded_tee = _load("ats_inventory_bounded_tee", BOUNDED_TEE)


def test_shell_surfaces_parse() -> None:
    for path in (RUNNER, CONTROL, INSTALLER, RECEIVER, REMOTE):
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_app_jwt_is_short_lived_and_signed_without_key_material_in_argv(
    tmp_path: Path, monkeypatch
) -> None:
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        assert kwargs["input"].count(b".") == 1
        return SimpleNamespace(returncode=0, stdout=b"signature")

    monkeypatch.setattr(token_helper.subprocess, "run", fake_run)
    private_key = tmp_path / "private-key"
    private_key.write_text("secret-key-material", encoding="utf-8")
    jwt = token_helper.build_app_jwt("123", private_key, now=1_800_000_000)
    payload_segment = jwt.split(".")[1]
    padding = "=" * (-len(payload_segment) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_segment + padding))

    assert payload == {"iat": 1_799_999_940, "exp": 1_800_000_540, "iss": "123"}
    assert seen == [["openssl", "dgst", "-sha256", "-sign", str(private_key)]]
    assert "secret-key-material" not in " ".join(seen[0])


def test_status_is_atomic_bounded_and_preserves_last_success(tmp_path: Path) -> None:
    state = tmp_path / "state"
    log = tmp_path / "run.log"
    report = {
        "data_only": True,
        "rows": 10,
        "coverage": {},
        "impact": {},
        "candidate_issues": {},
    }
    log.write_text(
        json.dumps({"event": "ats_inventory.complete", "report": report}) + "\n",
        encoding="utf-8",
    )
    first = status_helper.record(
        state,
        log,
        return_code=0,
        requested_mode="report",
        effective_mode="report",
        rollout_cap=1,
        started_at=100,
        finished_at=120,
    )
    assert first["last_attempt_success"] == 1
    assert first["last_attempt_degraded"] == 0
    assert first["last_success_unixtime"] == 120
    assert first["last_success_report"] == report
    assert os.stat(state / "status" / "current.json").st_mode & 0o777 == 0o640

    log.write_text("container failed before report\n", encoding="utf-8")
    failed = status_helper.record(
        state,
        log,
        return_code=1,
        requested_mode="refill",
        effective_mode="refill",
        rollout_cap=5,
        started_at=130,
        finished_at=140,
    )
    assert failed["last_attempt_success"] == 0
    assert failed["last_success_unixtime"] == 120
    assert failed["report"] is None
    assert failed["last_success_report"] == report

    for number in range(150, 190):
        status_helper.record(
            state,
            log,
            return_code=1,
            requested_mode="report",
            effective_mode="report",
            rollout_cap=1,
            started_at=number - 1,
            finished_at=number,
        )
    assert len(list((state / "status" / "history").glob("*.json"))) == 32


def test_bounded_logger_mirrors_stream_and_retains_parseable_tail(tmp_path: Path) -> None:
    output = tmp_path / "run.log"
    completion = json.dumps(
        {"event": "ats_inventory.complete", "report": {"data_only": True}}
    ).encode()
    source = b"x" * 5000 + b"\n" + completion + b"\n"

    result = subprocess.run(
        [
            sys.executable,
            str(BOUNDED_TEE),
            "--output",
            str(output),
            "--max-bytes",
            "2048",
        ],
        input=source,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == source
    assert output.stat().st_size <= 2048
    assert output.read_bytes() == completion + b"\n"


def test_rate_limited_complete_report_is_degraded_not_fresh_success(tmp_path: Path) -> None:
    state = tmp_path / "state"
    log = tmp_path / "run.log"
    report = {
        "data_only": True,
        "candidate_issues": {"status": "rate_limited_preflight"},
    }
    log.write_text(
        json.dumps({"event": "ats_inventory.complete", "report": report}) + "\n",
        encoding="utf-8",
    )

    status = status_helper.record(
        state,
        log,
        return_code=0,
        requested_mode="refill",
        effective_mode="refill",
        rollout_cap=1,
        started_at=100,
        finished_at=120,
    )

    assert status["last_attempt_success"] == 0
    assert status["last_attempt_degraded"] == 1
    assert status["last_success_unixtime"] == 0
    assert status["report"] == report


def test_runner_uses_immutable_image_ephemeral_token_and_bounded_resources() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert 'image="ghcr.io/colophon-group/jobseek-crawler:${tag}"' in source
    assert "ghcr.io/colophon-group/jobseek-crawler:latest" not in source
    assert "jobseek-ats-inventory-host.lock" in source
    assert "flock -n 9" in source
    assert "--read-only" in source
    assert "--cap-drop ALL" in source
    assert "--security-opt no-new-privileges" in source
    assert "--memory 1536m" in source
    assert "--cpus 1.0" in source
    assert "--pids-limit 256" in source
    assert "type=bind,src=$CACHE_ROOT,dst=/state/cache" in source
    assert "type=bind,src=$STATE_ROOT,dst=/state" not in source
    assert "com.docker.compose.oneoff" not in source
    assert "com.docker.compose.project" not in source
    assert "mktemp /run/lock/jobseek-ats-inventory-log" in source
    assert "run_phase 9900s data off 0" in source
    assert 'run_phase 2700s github "$effective_mode" 1' in source
    assert "jobseek-ats-inventory-bounded-tee" in source
    assert "--max-bytes 16777216" in source
    assert source.index("run_phase 9900s data off 0") < source.index(
        "jobseek-ats-inventory-github-token"
    )
    assert "--impact" in source
    assert '--candidate-issues "$candidate_mode"' in source
    assert '--queue-rollout-cap "$rollout_cap"' in source
    assert "--github-token-file /run/credentials/github-token" in source
    assert "GH_TOKEN=" not in source
    assert "--env-file" not in source
    assert "kalil0321/ats-scrapers" not in source
    assert "pip install" not in source
    assert "npm install" not in source
    assert "writes-disabled" in source
    assert "effective_mode=report" in source
    assert source.count("apply_write_gate") >= 3
    assert source.index("STATUS_ARMED=1") < source.index('[[ -r "$CONFIG" ]]')
    assert source.rindex("apply_write_gate") < source.index(
        'run_phase 2700s github "$effective_mode" 1'
    )


def test_control_and_installer_are_fail_closed_and_rollback_safe() -> None:
    control = CONTROL.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "disable)" in control
    assert "systemctl stop jobseek-ats-inventory.service" in control
    assert "cache and ledger retained" in control
    assert "configure)" in control and "enable)" in control
    assert "1|5|25" in control
    assert "writes-disabled" in control
    assert "restore_previous" in installer
    assert "stop_unit_if_present jobseek-ats-inventory.timer" in installer
    assert "stop_unit_if_present jobseek-ats-inventory.service" in installer
    quiesce = installer[
        installer.index("stop_unit_if_present jobseek-ats-inventory.timer") : installer.index(
            'install -d -o root -g deploy -m 0750 "$STATE_ROOT"'
        )
    ]
    assert "|| true" not in quiesce
    assert "SERVICE_WAS_ACTIVE" in installer
    assert 'install -d -o root -g deploy -m 0750 "$STATE_ROOT"' in installer
    assert "TIMER_WAS_ENABLED" in installer and "TIMER_WAS_ACTIVE" in installer
    assert "systemd-analyze verify" in installer
    assert "systemctl enable jobseek-ats-inventory.timer" in installer
    assert "systemctl start jobseek-ats-inventory.timer" in installer
    assert "ATS_INVENTORY_MODE=report" in installer
    assert "ATS_INVENTORY_ROLLOUT_CAP=1" in installer
    assert 'install -o root -g deploy -m 0640 /dev/null "$CONFIG_ROOT/writes-disabled"' in installer
    assert "jobseek-crawler-mutation.lock" in installer
    assert ".crawler-deploy-success.env" in installer
    assert "acceptance-crawler.env" in installer
    assert "acceptance-cache" in installer
    files = installer.partition("FILES=(")[2].partition(")")[0]
    assert "writes-disabled" not in files
    runner = RUNNER.read_text(encoding="utf-8")
    assert '[[ -e "$WRITE_DISABLED" || -e "$ACCEPTANCE_PIN" ]]' in runner
    assert 'exec 8<"$CRAWLER_LOCK"' in installer
    assert 'exec 8>"$CRAWLER_LOCK"' not in installer
    assert 'chown deploy:deploy "$CRAWLER_LOCK"' in installer
    acceptance = installer.index("systemctl start jobseek-ats-inventory.service")
    disarm = installer.index("ROLLBACK_ARMED=0")
    assert acceptance < disarm
    assert 'payload["last_attempt_success"] == 1' in installer
    assert 'payload["effective_mode"] == "report"' in installer
    assert "GitHub App private key is not PEM encoded" in installer
    assert "JOBSEEK_GITHUB_APP_PRIVATE_KEY_FILE" in installer
    receiver = RECEIVER.read_text(encoding="utf-8")
    assert '"$payload_size" -le 131072' in receiver
    assert "${#fields[@]} -eq 6" in receiver
    assert "JOBSEEK_EXPECTED_CRAWLER_IMAGE_TAG" in receiver
    assert "JOBSEEK_EXPECTED_CRAWLER_DEPLOY_REVISION" in receiver


def test_systemd_timer_is_daily_persistent_randomized_and_hardened() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")
    for credential in (
        "github-app-id",
        "github-app-installation-id",
        "github-app-private-key",
    ):
        assert f"LoadCredential={credential}:" in service
    assert "User=deploy" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in service
    assert "ReadWritePaths=/run/lock /var/lib/jobseek-ats-inventory" in service
    assert "OnCalendar=*-*-* 03:00:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=45m" in timer


def test_workflow_uses_protected_app_credentials_native_ssh_and_provisions_label() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "environment: production" in workflow
    assert "issues: write" in workflow
    assert "gh label create source:ats-inventory" in workflow
    assert "ATS_INVENTORY_GITHUB_APP_ID" in workflow
    assert "ATS_INVENTORY_GITHUB_APP_INSTALLATION_ID" in workflow
    assert "ATS_INVENTORY_GITHUB_APP_PRIVATE_KEY" in workflow
    assert "HETZNER_BACKUP_KNOWN_HOSTS" in workflow
    assert "deploy-remote.sh" in workflow
    assert "expected_tag=current" in workflow
    assert "expected_revision=current" in workflow
    assert "derive-crawler-build-version.mjs" in workflow
    assert "fetch-depth: 0" in workflow
    assert 'git diff --name-only "$BEFORE_SHA" "$GITHUB_SHA"' in workflow
    assert ".github/workflows/deploy-crawler-browser.yml" in workflow
    assert "timeout-minutes: 355" in workflow
    assert "appleboy/" not in workflow
    checkout = [line for line in workflow.splitlines() if "uses: actions/checkout@" in line]
    assert checkout and all("@v" not in line for line in checkout)
    assert "uses: astral-sh/setup-uv@c771a70" in workflow


def test_remote_deploy_waits_for_exact_image_before_quiescing_install() -> None:
    source = REMOTE.read_text(encoding="utf-8")
    image_gate = source.index("for ((attempt = 1; attempt <= 120; attempt++))")
    install = source.index("install-host-from-stdin.sh")
    assert image_gate < install
    gate = source[image_gate:install]
    assert "jobseek-crawler-mutation.lock" in source[:install]
    assert ".crawler-deploy-success.env" in source[:install]
    assert "CRAWLER_IMAGE_TAG" in gate
    assert "JOBSEEK_DEPLOY_REVISION" in gate
    assert 'exec 8<"$lock"' in source[:install]
    assert 'exec 8>"$lock"' not in source[:install]
    assert 'chown deploy:deploy "$lock"' in source[:install]
    assert "/home/deploy/.env" not in gate
    assert " 60m " in source
    assert " 250m " in source
    assert " 150m " not in source
    assert " 5h " not in source


def test_runner_uses_only_committed_release_or_transactional_acceptance_pin() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "DEPLOY_SUCCESS=/home/deploy/.crawler-deploy-success.env" in source
    assert 'ACCEPTANCE_PIN="$STATE_ROOT/acceptance-crawler.env"' in source
    assert 'release_file="$DEPLOY_SUCCESS"' in source
    assert 'release_file="$ACCEPTANCE_PIN"' in source
    assert "JOBSEEK_DEPLOY_REVISION" in source
    assert "/home/deploy/.env" not in source
