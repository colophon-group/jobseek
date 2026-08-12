"""Tests for the fail-closed Hetzner host-hygiene contract."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "jobseek-host-hygiene.py"
INSTALLER = ROOT / "deploy" / "host-hygiene" / "install-host.sh"
TRANSPORT = ROOT / "deploy" / "host-hygiene" / "run-remote.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-host-hygiene.yml"


def _load():
    spec = importlib.util.spec_from_file_location("jobseek_host_hygiene", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hygiene = _load()
CONTAINER_ID = "a" * 64
IMAGE_ID = f"sha256:{'b' * 64}"
CREATED_AT = "2026-07-27T10:00:00.123456789Z"
FINISHED_AT = "2026-07-27T10:00:02.123456789Z"


def _container(*, managed: bool = False, name: str = "naughty_darwin") -> dict:
    labels = {"jobseek.managed-by": "fixture"} if managed else {}
    return {
        "Id": CONTAINER_ID,
        "Image": IMAGE_ID,
        "Name": f"/{name}",
        "Created": CREATED_AT,
        "Config": {"Labels": labels},
        "State": {
            "Status": "exited",
            "Running": False,
            "Paused": False,
            "Restarting": False,
            "Dead": False,
            "ExitCode": 1,
            "FinishedAt": FINISHED_AT,
        },
        "HostConfig": {
            "LogConfig": {
                "Type": "json-file",
                "Config": {"max-size": "50m", "max-file": "3"},
            }
        },
    }


@pytest.mark.parametrize("role", hygiene.ROLES)
def test_repository_journal_policy_matches_role_contract(role: str) -> None:
    parser = hygiene.configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(ROOT / f"deploy/host-hygiene/journald/{role}.conf", encoding="utf-8")

    assert dict(parser.items("Journal")) == hygiene.EXPECTED_JOURNAL_POLICY[role]


def test_verify_journal_cli_fails_when_effective_policy_is_not_exact(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        hygiene,
        "_journal_findings",
        lambda role, policy_path: [
            {"kind": "journal_policy", "detail": "effective settings are overridden"}
        ],
    )

    assert hygiene.main(["verify-journal", "--role", "crawler"]) == 1
    assert "effective settings are overridden" in capsys.readouterr().out


def test_journal_policy_is_role_specific_and_requires_root_owned_exact_file(
    tmp_path: Path, monkeypatch
) -> None:
    policy = tmp_path / "policy.conf"
    policy.write_text(
        (ROOT / "deploy/host-hygiene/journald/typesense.conf").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    effective = policy.read_text(encoding="utf-8")
    policy.chmod(0o644)
    metadata = policy.stat()
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self: (
            SimpleNamespace(
                st_uid=0,
                st_gid=0,
                st_mode=metadata.st_mode,
            )
            if self == policy
            else metadata
        ),
    )

    assert hygiene._journal_findings("typesense", policy, effective_config=effective) == []
    findings = hygiene._journal_findings("crawler", policy, effective_config=effective)
    assert findings == [
        {"kind": "journal_policy", "detail": "settings differ from role budget"},
        {"kind": "journal_policy", "detail": "effective settings are overridden"},
    ]

    overridden = effective + "\n[Journal]\nSystemMaxUse=9G\n"
    assert hygiene._journal_findings("typesense", policy, effective_config=overridden) == [
        {"kind": "journal_policy", "detail": "effective settings are overridden"}
    ]


def test_unmanaged_exited_container_is_reported_but_managed_one_is_not(monkeypatch) -> None:
    containers = [_container(), _container(managed=True, name="managed-fixture")]

    def run(command):
        if command[:3] == ["docker", "ps", "-aq"]:
            return hygiene.CommandResult(0, f"{CONTAINER_ID}\n{'c' * 64}\n", "")
        if command[:2] == ["docker", "inspect"]:
            return hygiene.CommandResult(0, json.dumps(containers), "")
        raise AssertionError(command)

    monkeypatch.setattr(hygiene, "_run", run)
    assert hygiene._exited_container_findings() == [
        {"kind": "unmanaged_exited_container", "detail": CONTAINER_ID[:12]}
    ]


def test_standalone_log_conformance_requires_exact_bounded_options(monkeypatch) -> None:
    container = _container(managed=True, name="postgres")
    container["State"]["Running"] = True

    monkeypatch.setattr(
        hygiene,
        "_run",
        lambda command: hygiene.CommandResult(0, json.dumps([container]), ""),
    )
    assert hygiene._standalone_log_findings("postgresql") == []

    container["HostConfig"]["LogConfig"]["Config"] = {}
    assert hygiene._standalone_log_findings("postgresql") == [
        {"kind": "standalone_container_log", "detail": "postgres"}
    ]


def _masked_unit_run(command):
    if command[:2] == ["systemctl", "show"]:
        unit = command[2]
        if unit == hygiene.CANONICAL_RECONCILIATION_TIMER:
            output = "LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n"
        else:
            output = "LoadState=masked\nActiveState=inactive\nUnitFileState=masked\n"
        return hygiene.CommandResult(0, output, "")
    if command[:2] == ["systemctl", "is-enabled"]:
        return hygiene.CommandResult(1, "masked\n", "")
    if command[:2] == ["systemctl", "is-failed"]:
        return hygiene.CommandResult(1, "inactive\n", "")
    if command[:2] == ["systemctl", "list-unit-files"]:
        return hygiene.CommandResult(
            0,
            "jobseek-crawler-reconciliation.timer enabled enabled\n"
            "jobseek-reconciliation-typesense-catchup.timer masked enabled\n",
            "",
        )
    raise AssertionError(command)


def test_retired_reconciliation_units_are_masked_without_duplicate_timer(monkeypatch) -> None:
    def run(command):
        return _masked_unit_run(command)

    monkeypatch.setattr(hygiene, "_run", run)
    assert hygiene._retired_reconciliation_findings() == []

    def duplicate_run(command):
        result = run(command)
        if command[:2] == ["systemctl", "list-unit-files"]:
            return hygiene.CommandResult(
                0, result.stdout + "jobseek-reconciliation-shadow.timer enabled enabled\n", ""
            )
        return result

    monkeypatch.setattr(hygiene, "_run", duplicate_run)
    assert hygiene._retired_reconciliation_findings()[-1] == {
        "kind": "duplicate_reconciliation_timer",
        "detail": "jobseek-reconciliation-shadow.timer",
    }


def test_not_found_load_state_accepts_only_exact_dev_null_mask(tmp_path: Path, monkeypatch) -> None:
    service = hygiene.RETIRED_RECONCILIATION_UNITS[0]
    (tmp_path / service).symlink_to("/dev/null")

    def run(command):
        if command[:3] == ["systemctl", "show", service]:
            return hygiene.CommandResult(
                0,
                "LoadState=not-found\nActiveState=inactive\nUnitFileState=masked\n",
                "",
            )
        return _masked_unit_run(command)

    monkeypatch.setattr(hygiene, "_run", run)
    assert hygiene._retired_reconciliation_findings(unit_root=tmp_path) == []


@pytest.mark.parametrize("mask_kind", ["absent", "regular", "wrong-target"])
def test_not_found_load_state_rejects_invalid_exact_mask(
    mask_kind: str, tmp_path: Path, monkeypatch
) -> None:
    service = hygiene.RETIRED_RECONCILIATION_UNITS[0]
    mask_path = tmp_path / service
    if mask_kind == "regular":
        mask_path.write_text("/dev/null\n", encoding="utf-8")
    elif mask_kind == "wrong-target":
        wrong_target = tmp_path / "wrong-target"
        wrong_target.write_text("", encoding="utf-8")
        mask_path.symlink_to(wrong_target)

    def run(command):
        if command[:3] == ["systemctl", "show", service]:
            return hygiene.CommandResult(
                0,
                "LoadState=not-found\nActiveState=inactive\nUnitFileState=masked\n",
                "",
            )
        return _masked_unit_run(command)

    monkeypatch.setattr(hygiene, "_run", run)
    assert {"kind": "retired_reconciliation_unit", "detail": service} in (
        hygiene._retired_reconciliation_findings(unit_root=tmp_path)
    )


@pytest.mark.parametrize(
    ("property_output", "enabled_output", "failed_status"),
    [
        ("LoadState=not-found\nActiveState=active\nUnitFileState=masked\n", "masked\n", 1),
        ("LoadState=not-found\nActiveState=inactive\nUnitFileState=disabled\n", "masked\n", 1),
        ("LoadState=not-found\nActiveState=inactive\nUnitFileState=masked\n", "disabled\n", 1),
        ("LoadState=not-found\nActiveState=inactive\nUnitFileState=masked\n", "masked\n", 0),
    ],
)
def test_not_found_load_state_requires_every_independent_state_gate(
    property_output: str,
    enabled_output: str,
    failed_status: int,
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = hygiene.RETIRED_RECONCILIATION_UNITS[0]
    (tmp_path / service).symlink_to("/dev/null")

    def run(command):
        if command[:3] == ["systemctl", "show", service]:
            return hygiene.CommandResult(0, property_output, "")
        if command[:3] == ["systemctl", "is-enabled", service]:
            return hygiene.CommandResult(1, enabled_output, "")
        if command[:3] == ["systemctl", "is-failed", service]:
            return hygiene.CommandResult(failed_status, "inactive\n", "")
        return _masked_unit_run(command)

    monkeypatch.setattr(hygiene, "_run", run)
    assert not hygiene._retired_unit_is_safely_masked(service, unit_root=tmp_path)


def test_cleanup_is_dry_run_by_default_and_removes_only_full_reviewed_identity(
    monkeypatch,
) -> None:
    container = _container()
    commands: list[list[str]] = []
    removed = False

    def run(command):
        nonlocal removed
        command = list(command)
        commands.append(command)
        if command[:2] == ["docker", "inspect"]:
            if removed:
                return hygiene.CommandResult(1, "", "not found")
            return hygiene.CommandResult(0, json.dumps([container]), "")
        if command[:3] == ["docker", "rm", "--"]:
            removed = True
            return hygiene.CommandResult(0, f"{CONTAINER_ID}\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(hygiene, "_run", run)
    monkeypatch.setattr(hygiene.os, "geteuid", lambda: 0)
    base = {
        "role": "postgresql",
        "container_id": CONTAINER_ID,
        "image_id": IMAGE_ID,
        "created_at": CREATED_AT,
        "finished_at": FINISHED_AT,
        "exit_code": 1,
    }
    dry_run = hygiene.remove_exited_container(SimpleNamespace(**base, execute=False))
    assert dry_run["removed"] is False
    assert not any(command[:2] == ["docker", "rm"] for command in commands)

    commands.clear()
    result = hygiene.remove_exited_container(SimpleNamespace(**base, execute=True))
    assert result["removed"] is True
    assert ["docker", "rm", "--", CONTAINER_ID] in commands
    assert all("-f" not in command and "-v" not in command for command in commands)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"container_id": "short"}, "container ID"),
        ({"image_id": f"sha256:{'c' * 64}"}, "identity changed"),
        ({"created_at": "different"}, "identity changed"),
        ({"finished_at": "different"}, "identity changed"),
        ({"exit_code": 0}, "identity changed"),
    ],
)
def test_cleanup_fails_closed_when_any_identity_field_differs(
    override, message, monkeypatch
) -> None:
    monkeypatch.setattr(
        hygiene,
        "_run",
        lambda command: hygiene.CommandResult(0, json.dumps([_container()]), ""),
    )
    values = {
        "container_id": CONTAINER_ID,
        "image_id": IMAGE_ID,
        "created_at": CREATED_AT,
        "finished_at": FINISHED_AT,
        "exit_code": 1,
        **override,
    }
    with pytest.raises(hygiene.HygieneError, match=message):
        hygiene._validated_cleanup_inventory(**values)


def test_cleanup_rejects_protected_name_even_with_matching_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        hygiene,
        "_run",
        lambda command: hygiene.CommandResult(
            0, json.dumps([_container(name="postgres-rollback-pre-ingress")]), ""
        ),
    )
    with pytest.raises(hygiene.HygieneError, match="protected"):
        hygiene._validated_cleanup_inventory(
            container_id=CONTAINER_ID,
            image_id=IMAGE_ID,
            created_at=CREATED_AT,
            finished_at=FINISHED_AT,
            exit_code=1,
        )


def test_cleanup_execute_requires_root_before_docker_inspection(monkeypatch) -> None:
    monkeypatch.setattr(hygiene.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        hygiene,
        "_run",
        lambda command: pytest.fail(f"Docker must not run: {command}"),
    )
    args = SimpleNamespace(
        role="postgresql",
        container_id=CONTAINER_ID,
        image_id=IMAGE_ID,
        created_at=CREATED_AT,
        finished_at=FINISHED_AT,
        exit_code=1,
        execute=True,
    )

    with pytest.raises(hygiene.HygieneError, match="must run as root"):
        hygiene.remove_exited_container(args)


def test_installer_has_exact_unit_allowlist_and_no_broad_docker_cleanup() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert "jobseek-reconciliation-typesense-catchup.service" in script
    assert "jobseek-reconciliation-typesense-catchup.timer" in script
    assert "jobseek-crawler-reconciliation.timer" in script
    assert "canonical_timer_is_healthy" in script
    assert "systemctl reset-failed" in script
    assert "ln -s /dev/null" in script
    assert 'verify-retired-unit --unit "$unit"' in script
    assert "docker rm" not in script
    assert "docker container prune" not in script
    assert "docker system prune" not in script
    assert "rm -rf" not in script


def test_workflow_keeps_mutation_manual_and_cleanup_identity_bound() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")

    assert "  push:" not in workflow
    assert "  schedule:" in workflow
    assert "github.event_name == 'schedule'" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.action == 'apply'" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.action == 'cleanup'" in workflow
    for field in (
        "--container-id",
        "--image-id",
        "--created-at",
        "--finished-at",
        "--exit-code",
    ):
        assert field in transport
    assert "--execute" in transport
    assert "--name" not in transport
    assert "docker rm" not in workflow
    for action in (
        "actions/checkout",
        "astral-sh/setup-uv",
    ):
        matching = [line for line in workflow.splitlines() if f"uses: {action}@" in line]
        assert matching
        assert all("@v" not in line and "@main" not in line for line in matching)


def test_workflow_executes_uploaded_payload_only_from_root_owned_trust_boundary() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")

    assert "/tmp/jobseek-host-hygiene" not in workflow + transport
    assert "/var/lib/jobseek-host-hygiene/staging/" in transport
    assert "install -d -o root -g root -m 0700" in transport
    assert '[[ -d "$directory" && ! -L "$directory" ]]' in transport
    assert "stat -c '%u:%g:%a'" in transport
    assert "== 0:0:700" in transport
    assert 'find "$stage" -xdev -type l -print -quit' in transport
    assert "! -user root -o ! -group root" in transport
    assert '[[ -f "$payload" && ! -L "$payload" ]]' in transport
    assert 'bash --noprofile --norc "$installer"' in transport
    assert "tar --create --gzip --file -" in transport
    assert "tar --extract --gzip --file - --directory '$stage'" in transport


def test_workflow_pins_role_specific_host_identity_on_every_connection() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")
    combined = workflow + transport

    assert "appleboy/" not in combined
    assert "fingerprint" not in combined.lower()
    assert "accept-new" not in combined
    assert "ssh-keyscan" not in combined
    assert "StrictHostKeyChecking=yes" in transport
    assert 'ssh-keygen -F "$TARGET_HOST"' in transport
    assert workflow.count("bash deploy/host-hygiene/run-remote.sh") == 7

    assert "secrets[" not in workflow
    assert workflow.count("- role: crawler") == 2
    assert workflow.count("- role: postgresql") == 2
    assert workflow.count("- role: typesense") == 2
    for host_secret, known_hosts_secret, expected_connections in (
        ("HETZNER_HOST", "HETZNER_CRAWLER_KNOWN_HOSTS", 2),
        ("HETZNER_POSTGRES_HOST", "HETZNER_BACKUP_KNOWN_HOSTS", 3),
        ("HETZNER_TYPESENSE_HOST", "HETZNER_TYPESENSE_KNOWN_HOSTS", 2),
    ):
        assert (
            workflow.count(f"TARGET_HOST: ${{{{ secrets.{host_secret} }}}}") == expected_connections
        )
        expected_known_hosts = f"SSH_KNOWN_HOSTS: ${{{{ secrets.{known_hosts_secret} }}}}"
        assert workflow.count(expected_known_hosts) == expected_connections
    assert "&& secrets." not in workflow
    assert "HETZNER_POSTGRES_KNOWN_HOSTS" not in combined
    assert "SSH_FINGERPRINT" not in combined
