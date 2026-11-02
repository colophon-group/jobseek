"""Check the fixed, source-scoped PostgreSQL reader before host installation."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.export_cursor_fence import _CDC_CUTOFF_SQL, CDC_WRITER_BARRIER_ID

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/networking/justup-feed-readonly-role.py"
SPEC = importlib.util.spec_from_file_location("justup_feed_readonly_role", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
role = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(role)


def audit_evidence(*, taxonomy: int = 0) -> dict[str, object]:
    return {
        "login": True,
        "inherit": True,
        "superuser": False,
        "createdb": False,
        "createrole": False,
        "replication": False,
        "bypassrls": False,
        "connectionLimit": 2,
        "databaseConnect": True,
        "databaseCreate": False,
        "schemaCreate": False,
        "scramPassword": True,
        "taxonomyCount": taxonomy,
        "memberships": [],
        "schemaUsage": ["justup_feed", "public"],
        "writableSchemas": [],
        "settings": sorted(role.ROLE_SETTINGS),
        "readableTables": [role.VIEW],
        "readableColumns": sorted(
            {
                f"public.{table}.{column}"
                for table, columns in (
                    role.CORE_COLUMNS | (role.TAXONOMY_COLUMNS if taxonomy == 4 else {})
                ).items()
                for column in columns
            }
            | {f"{role.VIEW}.{column}" for column in ("posting_id", "locale", "hash", "html")}
        ),
        "writableTables": [],
        "accessibleSequences": [],
        "callableRoutines": [
            {
                "schema": "justup_feed",
                "name": "capture_cdc_snapshot_cutoff",
                "owner": role.OWNER,
                "securityDefiner": True,
            }
        ],
    }


def boundary_evidence() -> dict[str, object]:
    return {
        "ownerLogin": False,
        "ownerSuperuser": False,
        "ownerCreateRole": False,
        "ownerCreateDb": False,
        "ownerReplication": False,
        "ownerBypassRls": False,
        "ownerMemberships": ["pg_read_all_stats"],
        "ownerInboundMemberships": 0,
        "schemaOwner": role.OWNER,
        "schemaAcl": [f"{role.OWNER}:CREATE", f"{role.OWNER}:USAGE", f"{role.ROLE}:USAGE"],
        "functionCount": 1,
        "functionOwner": role.OWNER,
        "functionSecurityDefiner": True,
        "functionVolatility": "v",
        "functionParallel": "u",
        "functionLeakproof": False,
        "functionSource": role.CUTOFF_FUNCTION_BODY,
        "functionSettings": ["search_path=pg_catalog, pg_temp"],
        "functionAcl": [f"{role.OWNER}:EXECUTE", f"{role.ROLE}:EXECUTE"],
        "viewOwner": "crawler",
        "viewSecurityBarrier": True,
        "viewDefinition": role.VIEW_DEFINITION,
        "viewAcl": [
            "crawler:DELETE",
            "crawler:INSERT",
            "crawler:REFERENCES",
            "crawler:SELECT",
            "crawler:TRIGGER",
            "crawler:TRUNCATE",
            "crawler:UPDATE",
            f"{role.ROLE}:SELECT",
        ],
    }


def test_install_sql_limits_role_to_source_columns_and_cutoff_function() -> None:
    sql = role.install_sql("A" * 64)
    assert "CONNECTION LIMIT 2" in sql
    assert "GRANT SELECT (" in sql
    assert "ON TABLE public.company TO justup_feed_readonly" in sql
    assert "GRANT pg_read_all_stats TO justup_feed_cutoff_owner" in sql
    assert "GRANT pg_read_all_stats TO justup_feed_readonly" not in sql
    assert "WITH (security_barrier=true)" in sql
    assert "GRANT SELECT ON TABLE justup_feed.uploaded_description" in sql
    assert "GRANT SELECT ON TABLE public.descriptions" not in sql
    assert "PASSWORD 'SCRAM-SHA-256$4096:" in sql
    assert "A" * 64 not in sql
    assert "GRANT SELECT ON ALL TABLES" not in sql
    assert "GRANT INSERT" not in sql
    with pytest.raises(role.RoleError):
        role.install_sql("A' ; DROP TABLE job_posting; --")


def test_protected_password_matches_only_its_scram_verifier() -> None:
    verifier = role.scram_verifier("A" * 64, b"0" * 16)
    assert role.password_matches_scram("A" * 64, verifier)
    assert not role.password_matches_scram("B" * 64, verifier)
    assert not role.password_matches_scram("A" * 64, "not-a-scram-verifier")


def test_privileged_cutoff_is_identical_to_crawler_writer_barrier() -> None:
    assert role.CDC_WRITER_BARRIER_ID == CDC_WRITER_BARRIER_ID
    assert role.CUTOFF_QUERY.replace("pg_catalog.", "") == _CDC_CUTOFF_SQL.replace(
        "$1::bigint", f"{CDC_WRITER_BARRIER_ID}::bigint"
    )
    assert "SET search_path = pg_catalog, pg_temp" in role.install_sql("A" * 64)


@pytest.mark.parametrize("taxonomy", [0, 4])
def test_audit_accepts_only_exact_reader_contract(taxonomy: int) -> None:
    result = role.validate_audit(audit_evidence(taxonomy=taxonomy), boundary_evidence())
    assert result["role"] == "justup_feed_readonly"
    assert len(result["readableColumns"]) == 30 + sum(
        len(columns) for columns in role.TAXONOMY_COLUMNS.values()
    ) * (taxonomy == 4)


@pytest.mark.parametrize(
    ("field", "unexpected"),
    [
        ("memberships", ["pg_read_all_stats"]),
        ("readableTables", [role.VIEW, "public.job_board"]),
        ("readableColumns", ["public.company.extras"]),
        ("writableTables", ["public.job_posting"]),
        ("schemaUsage", ["justup_feed", "public", "private"]),
        ("accessibleSequences", ["public.secret_sequence"]),
        ("connectionLimit", -1),
        ("databaseCreate", True),
        ("scramPassword", False),
    ],
)
def test_audit_rejects_broader_or_unbounded_role(field: str, unexpected: object) -> None:
    evidence = audit_evidence()
    evidence[field] = unexpected
    with pytest.raises(role.RoleError):
        role.validate_audit(evidence, boundary_evidence())


def test_audit_rejects_partial_taxonomy_schema() -> None:
    with pytest.raises(role.RoleError, match="partial"):
        role.validate_audit(audit_evidence(taxonomy=2), boundary_evidence())


@pytest.mark.parametrize(
    "field",
    [
        "ownerMemberships",
        "functionSource",
        "functionAcl",
        "viewDefinition",
        "viewAcl",
        "viewSecurityBarrier",
    ],
)
def test_audit_rejects_privileged_boundary_drift(field: str) -> None:
    boundary = boundary_evidence()
    boundary[field] = None
    with pytest.raises(role.RoleError):
        role.validate_audit(audit_evidence(), boundary)


def test_audit_rejects_callable_external_security_definer() -> None:
    evidence = audit_evidence()
    evidence["callableRoutines"].append(
        {
            "schema": "public",
            "name": "private_data",
            "owner": "crawler",
            "securityDefiner": True,
        }
    )
    with pytest.raises(role.RoleError, match="unexpected privileged routine"):
        role.validate_audit(evidence, boundary_evidence())


def test_psql_hides_password_even_when_database_error_echoes_sql(monkeypatch) -> None:
    secret = "S" * 64

    def failed_run(args, **kwargs):
        assert tuple(args) == role.PSQL
        assert secret not in " ".join(args)
        assert secret not in kwargs["input"]
        return SimpleNamespace(returncode=1, stdout="", stderr=f"ERROR: {kwargs['input']}")

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(role.RoleError) as failure:
        role.psql(role.install_sql(secret))
    assert secret not in str(failure.value)


def test_stage_verifier_requires_exact_digest_checked_payloads(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    installer = stage / "deploy/networking/justup-feed-readonly-role.py"
    conformance = stage / "scripts/jobseek-ingress-conformance.py"
    for directory in (stage, installer.parent.parent, installer.parent, conformance.parent):
        directory.mkdir(exist_ok=True, mode=0o700)
    installer.write_bytes(b"fixed installer")
    conformance.write_bytes(b"fixed ingress audit")
    installer.chmod(0o600)
    conformance.chmod(0o600)
    installer_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    conformance_hash = hashlib.sha256(conformance.read_bytes()).hexdigest()
    role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())

    conformance.write_bytes(b"tampered")
    with pytest.raises(role.RoleError, match="digest"):
        role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())
    conformance.write_bytes(b"fixed ingress audit")
    extra = stage / "extra"
    extra.write_bytes(b"unexpected")
    with pytest.raises(role.RoleError, match="missing or extra"):
        role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())
    extra.unlink()
    conformance.unlink()
    with pytest.raises(role.RoleError, match="missing or extra"):
        role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())


def test_install_requires_no_pending_ingress_rollback(tmp_path: Path, monkeypatch) -> None:
    pending = tmp_path / "pending"
    monkeypatch.setattr(role, "INGRESS_PENDING", (pending,))

    def inactive(args, **_kwargs):
        if args[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout="inactive\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", inactive)
    role.assert_ingress_stable()
    pending.touch()
    with pytest.raises(role.RoleError, match="pending"):
        role.assert_ingress_stable()
    pending.unlink()

    def active_timer(args, **_kwargs):
        if args[0] == "systemctl" and args[-1] == "jobseek-ingress-rollback.timer":
            return SimpleNamespace(returncode=0, stdout="active\n")
        return inactive(args)

    monkeypatch.setattr(subprocess, "run", active_timer)
    with pytest.raises(role.RoleError, match="not quiescent"):
        role.assert_ingress_stable()

    def rollback_container(args, **_kwargs):
        if args[0] == "docker":
            return SimpleNamespace(returncode=0, stdout="postgres-rollback-pre-ingress\n")
        return inactive(args)

    monkeypatch.setattr(subprocess, "run", rollback_container)
    with pytest.raises(role.RoleError, match="rollback container"):
        role.assert_ingress_stable()
