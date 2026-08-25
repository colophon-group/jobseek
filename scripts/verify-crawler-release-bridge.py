#!/usr/bin/env python3
"""Verify optional legacy-to-v3 crawler release bridge provenance."""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import stat

SHA256 = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40}")


def fail(message: str) -> None:
    raise SystemExit(f"legacy bridge verification failed: {message}")


def require_regular(path: pathlib.Path, label: str) -> bytes:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        fail(f"{label} is missing")
    if not stat.S_ISREG(mode) or path.is_symlink():
        fail(f"{label} is unsafe")
    return path.read_bytes()


def lexists(path: pathlib.Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def parse_lines(content: bytes, label: str) -> list[str]:
    try:
        return content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        fail(f"{label} is not UTF-8: {error}")


def exact_value(lines: list[str], key: str, label: str) -> str:
    values = [line.removeprefix(f"{key}=") for line in lines if line.startswith(f"{key}=")]
    if len(values) != 1:
        fail(f"{label} must contain exactly one {key}")
    return values[0]


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def image_pattern(owner: str, image: str) -> re.Pattern[str]:
    return re.compile(rf"ghcr\.io/{re.escape(owner)}/{re.escape(image)}@sha256:[0-9a-f]{{64}}")


def verify(generation: pathlib.Path, owner: str) -> str:
    manifest_path = generation / "release.manifest"
    manifest = parse_lines(require_regular(manifest_path, "release manifest"), "release manifest")
    if exact_value(manifest, "RELEASE_FORMAT_VERSION", "release manifest") != "3":
        fail("bridge verifier requires release format 3")

    bridge_values = [
        line.removeprefix("LEGACY_BRIDGE_FORMAT_VERSION=")
        for line in manifest
        if line.startswith("LEGACY_BRIDGE_FORMAT_VERSION=")
    ]
    residue_paths = (
        "runtime-attestation.env",
        "legacy-source-compose.yml",
        "legacy-source-environment.env",
        "legacy-source-success.env",
        "legacy-source-images.override.yml",
    )
    legacy_lines = [line for line in manifest if line.startswith("LEGACY_")]
    if not bridge_values:
        if legacy_lines or any(lexists(generation / name) for name in residue_paths):
            fail("generic v3 release contains bridge residue")
        return "generic"
    if bridge_values != ["1"]:
        fail("bridge format is duplicated or unsupported")

    source_format = exact_value(manifest, "LEGACY_SOURCE_RELEASE_FORMAT", "release manifest")
    if source_format not in {"1", "2"}:
        fail("source release format is unsupported")
    required_legacy_keys = {
        "LEGACY_BRIDGE_FORMAT_VERSION",
        "LEGACY_BRIDGE_TRANSITIVE",
        "LEGACY_RUNTIME_ATTESTATION_SHA256",
        "LEGACY_SOURCE_RELEASE_FORMAT",
        "LEGACY_SOURCE_REVISION",
        "LEGACY_SOURCE_CRAWLER_IMAGE_REF",
        "LEGACY_SOURCE_COMPOSE_SHA256",
        "LEGACY_SOURCE_ENVIRONMENT_SHA256",
        "LEGACY_SOURCE_SUCCESS_SHA256",
    }
    if source_format == "2":
        required_legacy_keys.add("LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256")
    legacy_keys = [line.split("=", 1)[0] for line in legacy_lines]
    if len(legacy_keys) != len(set(legacy_keys)) or set(legacy_keys) != required_legacy_keys:
        fail("bridge metadata fields are missing, duplicated, or unexpected")

    transitive = exact_value(manifest, "LEGACY_BRIDGE_TRANSITIVE", "release manifest")
    if transitive not in {"0", "1"}:
        fail("bridge transitive flag is invalid")
    data_revision = exact_value(manifest, "DATA_REVISION", "release manifest")
    runtime = exact_value(
        parse_lines(require_regular(generation / "environment.env", "environment"), "environment"),
        "JOBSEEK_RUNTIME_CONTRACT_SHA256",
        "environment",
    )
    success_content = require_regular(generation / "success.env", "success marker")
    success_lines = parse_lines(success_content, "success marker")
    if exact_value(success_lines, "JOBSEEK_RUNTIME_CONTRACT_SHA256", "success marker") != runtime:
        fail("current runtime contract pair disagrees")
    if not REVISION.fullmatch(data_revision) or not SHA256.fullmatch(runtime):
        fail("current data/runtime identity is malformed")

    attestation_content = require_regular(
        generation / "runtime-attestation.env", "runtime attestation"
    )
    attestation_digest = exact_value(
        manifest, "LEGACY_RUNTIME_ATTESTATION_SHA256", "release manifest"
    )
    if (
        not SHA256.fullmatch(attestation_digest)
        or sha256(attestation_content) != attestation_digest
    ):
        fail("runtime attestation digest is mismatched")
    attestation = parse_lines(attestation_content, "runtime attestation")
    if len(attestation) < 4 or attestation[:3] != [
        "RUNTIME_ATTESTATION_FORMAT_VERSION=1",
        f"PREVIOUS_REVISION={data_revision}",
        f"RUNTIME_CONTRACT_SHA256={runtime}",
    ]:
        fail("runtime attestation identity is mismatched")
    compatible_revisions: list[str] = []
    for line in attestation[3:]:
        if not re.fullmatch(r"COMPATIBLE_REVISION=[0-9a-f]{40}", line):
            fail("runtime attestation contains an unexpected field")
        compatible_revisions.append(line.removeprefix("COMPATIBLE_REVISION="))
    if (
        not compatible_revisions
        or compatible_revisions[0] != data_revision
        or len(compatible_revisions) != len(set(compatible_revisions))
    ):
        fail("runtime attestation epoch is invalid")

    source_revision = exact_value(manifest, "LEGACY_SOURCE_REVISION", "release manifest")
    if not REVISION.fullmatch(source_revision) or source_revision not in compatible_revisions:
        fail("source revision is outside the attested runtime epoch")
    source_image = exact_value(manifest, "LEGACY_SOURCE_CRAWLER_IMAGE_REF", "release manifest")
    if not image_pattern(owner, "jobseek-crawler").fullmatch(source_image):
        fail("source crawler image is malformed")

    source_files = {
        "compose": require_regular(generation / "legacy-source-compose.yml", "source Compose"),
        "environment": require_regular(
            generation / "legacy-source-environment.env", "source environment"
        ),
        "success": require_regular(generation / "legacy-source-success.env", "source success"),
    }
    for label, key in (
        ("compose", "LEGACY_SOURCE_COMPOSE_SHA256"),
        ("environment", "LEGACY_SOURCE_ENVIRONMENT_SHA256"),
        ("success", "LEGACY_SOURCE_SUCCESS_SHA256"),
    ):
        expected = exact_value(manifest, key, "release manifest")
        if not SHA256.fullmatch(expected) or sha256(source_files[label]) != expected:
            fail(f"source {label} digest is mismatched")

    source_environment = parse_lines(source_files["environment"], "source environment")
    source_success = parse_lines(source_files["success"], "source success")
    if any(line.startswith("JOBSEEK_RUNTIME_CONTRACT_SHA256=") for line in source_environment):
        fail("source environment unexpectedly contains runtime evidence")
    if any(line.startswith("JOBSEEK_RUNTIME_CONTRACT_SHA256=") for line in source_success):
        fail("source success unexpectedly contains runtime evidence")
    source_identities = {}
    for key in (
        "CRAWLER_IMAGE_TAG",
        "CRAWLER_IMAGE_REF",
        "BROWSER_IMAGE_REF",
        "SHIM_IMAGE_REF",
        "JOBSEEK_DEPLOY_REVISION",
    ):
        environment_value = exact_value(source_environment, key, "source environment")
        if exact_value(source_success, key, "source success") != environment_value:
            fail(f"source evidence disagrees on {key}")
        source_identities[key] = environment_value
    if source_identities["JOBSEEK_DEPLOY_REVISION"] != source_revision:
        fail("source revision disagrees with source snapshots")
    if source_identities["CRAWLER_IMAGE_REF"] != source_image:
        fail("source crawler image disagrees with source snapshots")
    if not image_pattern(owner, "jobseek-crawler-browser").fullmatch(
        source_identities["BROWSER_IMAGE_REF"]
    ) or not image_pattern(owner, "jobseek-murmur-shim").fullmatch(
        source_identities["SHIM_IMAGE_REF"]
    ):
        fail("source browser/shim image identity is malformed")

    current_environment_content = require_regular(generation / "environment.env", "environment")
    current_environment = parse_lines(current_environment_content, "environment")
    for key in (
        "CRAWLER_IMAGE_TAG",
        "CRAWLER_IMAGE_REF",
        "BROWSER_IMAGE_REF",
        "JOBSEEK_DEPLOY_REVISION",
    ):
        if exact_value(current_environment, key, "environment") != source_identities[key]:
            fail(f"current release changed bridged {key}")
        if exact_value(success_lines, key, "success marker") != source_identities[key]:
            fail(f"current success changed bridged {key}")
    current_shim = exact_value(current_environment, "SHIM_IMAGE_REF", "environment")
    if exact_value(success_lines, "SHIM_IMAGE_REF", "success marker") != current_shim:
        fail("current environment/success disagree on SHIM_IMAGE_REF")
    if not image_pattern(owner, "jobseek-murmur-shim").fullmatch(current_shim):
        fail("current shim image identity is malformed")
    if exact_value(manifest, "COMPOSE_SHA256", "release manifest") != sha256(
        source_files["compose"]
    ):
        fail("current base Compose is not the legacy source Compose")

    has_override = exact_value(manifest, "HAS_IMAGE_OVERRIDE", "release manifest")
    image_override = (
        exact_value(manifest, "IMAGE_OVERRIDE_SHA256", "release manifest")
        if has_override == "1"
        else None
    )
    if has_override not in {"0", "1"}:
        fail("current image override flag is invalid")
    if source_format == "1":
        if lexists(generation / "legacy-source-images.override.yml"):
            fail("format-1 source contains image override residue")
        if transitive == "0" and has_override != "0":
            fail("initial format-1 bridge gained an image override")
    else:
        source_override = require_regular(
            generation / "legacy-source-images.override.yml", "source image override"
        )
        source_override_digest = exact_value(
            manifest, "LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256", "release manifest"
        )
        if (
            not SHA256.fullmatch(source_override_digest)
            or sha256(source_override) != source_override_digest
        ):
            fail("source image override digest is mismatched")
        if transitive == "0" and (has_override != "1" or image_override != source_override_digest):
            fail("initial format-2 bridge changed its image override")

    if transitive == "0":
        runtime_line = f"JOBSEEK_RUNTIME_CONTRACT_SHA256={runtime}\n".encode()
        if current_environment_content != source_files["environment"] + runtime_line:
            fail("initial bridge environment is not the exact attested source plus runtime")
        if success_content != source_files["success"] + runtime_line:
            fail("initial bridge success is not the exact attested source plus runtime")
        if require_regular(generation / "docker-compose.yml", "Compose") != source_files["compose"]:
            fail("initial bridge Compose differs from its source")
    return "bridge"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", required=True, type=pathlib.Path)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.owner):
        fail("owner is malformed")
    if not args.generation.is_absolute() or args.generation.is_symlink():
        fail("generation path is unsafe")
    print(verify(args.generation, args.owner))


if __name__ == "__main__":
    main()
