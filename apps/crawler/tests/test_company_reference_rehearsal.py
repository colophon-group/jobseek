from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = load(
    "rehearsal_validator", ROOT / "deploy/backups/web-postgresql/verify-rehearsal-bundle.py"
)
operations = load("rehearsal_operations", ROOT / "deploy/backups/web-postgresql/operations.py")


@pytest.fixture
def bundle(tmp_path):
    files = [
        "rehearse.mjs",
        "company-reference-dependencies.json",
        "drizzle/meta/_journal.json",
        "drizzle/0099_product_news_consent.sql",
        "drizzle/0100_company_references.sql",
    ]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n")
    hashes = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in files}
    manifest = {
        "version": 1,
        "sourceClean": True,
        "sourceRevision": "a" * 40,
        "runtimeImage": "node:24-alpine@sha256:" + "b" * 64,
        "files": hashes,
        "migrations": [
            {
                "tag": "0100_company_references",
                "createdAt": 1790985600000,
                "hash": hashes["drizzle/0100_company_references.sql"],
            }
        ],
    }
    return tmp_path, manifest


def verify(root, manifest, *, revision="a" * 40, tag="0100_company_references"):
    raw = json.dumps(manifest).encode()
    (root / "manifest.json").write_bytes(raw)
    return validator.verify_bundle(root, revision, hashlib.sha256(raw).hexdigest(), tag)


def test_exact_bundle_identity_and_resource_set(bundle):
    root, manifest = bundle
    identity = verify(root, manifest)
    assert identity["target"] == manifest["migrations"][0]
    assert identity["sourceRevision"] == "a" * 40


@pytest.mark.parametrize(
    "change",
    [
        "dirty",
        "revision",
        "runtime",
        "allowlist",
        "absolute",
        "traversal",
        "extra_resource",
        "missing_resource",
        "modified_resource",
        "symlink",
        "sql_hash",
    ],
)
def test_immutable_bundle_rejects_changed_boundary(bundle, change):
    root, manifest = bundle
    if change == "dirty":
        manifest["sourceClean"] = False
    elif change == "revision":
        manifest["sourceRevision"] = "c" * 40
    elif change == "runtime":
        manifest["runtimeImage"] = "node:latest"
    elif change == "allowlist":
        manifest["migrations"][0]["tag"] = "9999_arbitrary"
    elif change == "absolute":
        manifest["files"]["/tmp/other"] = "c" * 64
    elif change == "traversal":
        manifest["files"]["../other"] = "c" * 64
    elif change == "extra_resource":
        (root / "extra.mjs").write_text("extra")
    elif change == "missing_resource":
        (root / "rehearse.mjs").unlink()
    elif change == "modified_resource":
        (root / "rehearse.mjs").write_text("changed")
    elif change == "symlink":
        path = root / "rehearse.mjs"
        path.unlink()
        path.symlink_to(root / "company-reference-dependencies.json")
    elif change == "sql_hash":
        manifest["migrations"][0]["hash"] = "c" * 64
    with pytest.raises(ValueError):
        verify(root, manifest)


def proof_fixture():
    target = {"tag": "0100_company_references", "createdAt": 1790985600000, "hash": "d" * 64}
    identity = {
        "target": target,
        "runtimeImage": "node:24-alpine@sha256:" + "b" * 64,
        "manifestSha256": "c" * 64,
    }
    backup = {"archive_sha256": "e" * 64}
    retained = [
        "user",
        "session",
        "account",
        "verification",
        "user_preferences",
        "industry",
        "company",
        "company_description",
        "job_board",
        "saved_job",
        "application_interview",
        "followed_company",
        "company_request",
        "hiring_signal",
        "outreach_draft",
        "watchlist",
        "watchlist_company",
    ]
    proof = {
        "contract": "company_reference_archive_rehearsal",
        "outcome": "passed",
        "sourceRevision": "a" * 40,
        "runtimeImage": identity["runtimeImage"],
        "archiveSha256": backup["archive_sha256"],
        "manifestSha256": identity["manifestSha256"],
        "target": target,
        "preflight": "passed",
        "postflight": "passed",
        "preserved": {table: {"rows": 1, "digest": "f" * 64} for table in retained},
        "referenceRows": 1,
        "dependencies": {
            "phase": "bridge",
            "foreignKeyCount": 6,
            "lifecycleOwners": 4,
            "optionalForeignKeyCount": 0,
            "snapshot": "independent",
        },
    }
    return identity, backup, proof


def test_host_requires_exact_rehearsal_archive_and_preservation_proof():
    identity, backup, proof = proof_fixture()
    operations.validate_rehearsal_proof(
        proof, operations.ExpectedIdentity("a" * 40, {}), identity, backup
    )


@pytest.mark.parametrize(
    "change",
    [
        "archive",
        "source",
        "target",
        "cleanup_unknown",
        "missing_description",
        "negative_rows",
        "fk_coverage",
    ],
)
def test_host_rejects_incomplete_rehearsal_proof(change):
    identity, backup, proof = proof_fixture()
    if change == "archive":
        proof["archiveSha256"] = "b" * 64
    elif change == "source":
        proof["sourceRevision"] = "b" * 40
    elif change == "target":
        proof["target"] = {**proof["target"], "hash": "b" * 64}
    elif change == "cleanup_unknown":
        proof["outcome"] = "unknown"
    elif change == "missing_description":
        del proof["preserved"]["company_description"]
    elif change == "negative_rows":
        proof["preserved"]["saved_job"]["rows"] = -1
    elif change == "fk_coverage":
        proof["dependencies"]["foreignKeyCount"] = 5
    with pytest.raises(operations.OperationError):
        operations.validate_rehearsal_proof(
            proof, operations.ExpectedIdentity("a" * 40, {}), identity, backup
        )


def test_v1_and_v2_status_versions_remain_exact_and_v3_is_not_relabeled():
    old = {"table_count": 17}
    assert operations.company_reference_evidence_matches(old, {**old, "packet_version": 1})
    v2 = {
        **old,
        "company_reference_phase": "legacy",
        "company_reference_rows": 0,
        "company_reference_digest": "legacy",
    }
    assert operations.company_reference_evidence_matches(v2, {**v2, "packet_version": 2})
    assert not operations.company_reference_evidence_matches(
        v2, {**v2, "table_count": 18, "packet_version": 3}
    )


@pytest.mark.parametrize("unsafe", ["writable", "symlink"])
def test_host_rejects_replaceable_rehearsal_directory(tmp_path, unsafe):
    directory = tmp_path / "bundle"
    directory.mkdir()
    if unsafe == "writable":
        directory.chmod(0o777)
    else:
        directory.rmdir()
        directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(operations.OperationError):
        operations.require_root_directory(directory)


def test_final_bundle_retains_expansion_and_exact_contract_resources(bundle):
    root, manifest = bundle
    tag = "0101_company_reference_selection_contract"
    path = root / f"drizzle/{tag}.sql"
    path.write_text("reviewed contract fixture\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["files"][f"drizzle/{tag}.sql"] = digest
    manifest["migrations"].append({"tag": tag, "createdAt": 1790992800000, "hash": digest})
    assert verify(root, manifest)["target"] == manifest["migrations"][0]
    assert verify(root, manifest, tag=tag)["target"] == manifest["migrations"][1]
    manifest["migrations"][1]["hash"] = "f" * 64
    with pytest.raises(ValueError, match="target SQL"):
        verify(root, manifest, tag=tag)


def test_host_final_proof_requires_preserved_references_and_reference_phase():
    identity, backup, proof = proof_fixture()
    identity["target"]["tag"] = "0101_company_reference_selection_contract"
    proof["preserved"]["company_reference"] = {"rows": 2, "digest": "f" * 64}
    proof["referenceRows"] = 2
    with pytest.raises(operations.OperationError, match="dependency proof"):
        operations.validate_rehearsal_proof(
            proof, operations.ExpectedIdentity("a" * 40, {}), identity, backup
        )
    proof["dependencies"]["phase"] = "reference"
    operations.validate_rehearsal_proof(
        proof, operations.ExpectedIdentity("a" * 40, {}), identity, backup
    )
    proof["referenceRows"] = 1
    with pytest.raises(operations.OperationError, match="reference preservation count"):
        operations.validate_rehearsal_proof(
            proof, operations.ExpectedIdentity("a" * 40, {}), identity, backup
        )


@pytest.mark.parametrize("stage", ["preauthorize", "authorize"])
@pytest.mark.parametrize(
    "tag,confirmation,accepted",
    [
        ("0100_company_references", "REHEARSE-COMPANY-REFERENCE-0100", True),
        ("0101_company_reference_selection_contract", "REHEARSE-COMPANY-REFERENCE-0101", True),
        ("0100_company_references", "REHEARSE-COMPANY-REFERENCE-0101", False),
        ("0101_company_reference_selection_contract", "REHEARSE-COMPANY-REFERENCE-0100", False),
        ("9999_arbitrary", "REHEARSE-COMPANY-REFERENCE-0101", False),
    ],
)
def test_actual_dispatch_confirmation_binds_exact_rehearsal_target(
    stage, tag, confirmation, accepted
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/operate-web-postgresql-backup.yml").read_text()
    )
    script = workflow["jobs"][stage]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DISPATCH_ACTOR": "viktor-shcherb",
            "DISPATCH_TRIGGERING_ACTOR": "viktor-shcherb",
            "DISPATCH_EVENT": "workflow_dispatch",
            "DISPATCH_REF": "refs/heads/main",
            "DISPATCH_SHA": "a" * 40,
            "DISPATCH_MODE": "rehearse",
            "DISPATCH_CONFIRMATION": confirmation,
            "REHEARSAL_MIGRATION_TAG": tag,
        },
    )
    assert (result.returncode == 0) is accepted
