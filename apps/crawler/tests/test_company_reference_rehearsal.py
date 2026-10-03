from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

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
