from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REDIS_IMAGE = (
    "redis:8-alpine@sha256:978f0e01593e65eed801f2402944efcd936d43b5027e4908a7897baf88ed6241"
)
TYPESENSE_IMAGE = (
    "typesense/typesense:27.1@sha256:"
    "5c12af89130b8ee0be11541321ba8a3a7c7a538d7c6cd95e0409dc2d75ca6455"
)


def test_redis_and_murmur_shim_require_immutable_production_images() -> None:
    compose = (ROOT / "apps/crawler/docker-compose.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "apps/crawler/deploy.sh").read_text(encoding="utf-8")

    assert f"image: {REDIS_IMAGE}" in compose
    assert f'REDIS_IMAGE="{REDIS_IMAGE}"' in deploy
    assert "docker pull redis:8-alpine" not in deploy
    assert "SHIM_IMAGE_REF:?SHIM_IMAGE_REF must be an immutable GHCR digest" in compose
    assert "SHIM_IMAGE_TAG:-latest" not in compose
    assert "resolve_shim_image_ref" in deploy
    assert "duplicate SHIM_IMAGE_REF" in deploy
    assert "jobseek-murmur-shim@sha256:[0-9a-f]{64}" in deploy
    assert "SHIM_IMAGE_REF=${SHIM_IMAGE_REF}" in deploy
    assert deploy.index("resolve_shim_image_ref", deploy.index("activate_staged_deploy_specs")) < (
        deploy.index('cat > "$ENV_FILE"')
    )


def test_murmur_shim_deploy_promotes_and_persists_the_built_digest() -> None:
    workflow = (ROOT / ".github/workflows/deploy-murmur-shim.yml").read_text(encoding="utf-8")

    assert "image_digest: ${{ steps.build-shim.outputs.digest }}" in workflow
    assert "id: build-shim" in workflow
    assert (
        "SHIM_IMAGE_REF: ghcr.io/colophon-group/jobseek-murmur-shim@"
        "${{ needs.build.outputs.image_digest }}" in workflow
    )
    assert "previous_shim_ref=" in workflow
    assert "restoring the prior immutable image" in workflow
    assert 'SHIM_IMAGE_REF="$previous_shim_ref"' in workflow
    assert "test ! -L /home/deploy/.env" in workflow
    assert "awk '!/^SHIM_IMAGE_REF=/' /home/deploy/.env" in workflow
    assert "SHIM_IMAGE_REF=%s" in workflow
    assert "jobseek-murmur-shim:latest" in workflow  # Published compatibility tag only.
    deploy_script = workflow[workflow.index("script: |") :]
    assert "jobseek-murmur-shim:latest" not in deploy_script


def test_typesense_host_and_smoke_use_the_same_manifest_digest() -> None:
    installer = (ROOT / "deploy/typesense-host/install-host.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy-typesense-host.yml").read_text(encoding="utf-8")

    assert f"TYPESENSE_IMAGE={TYPESENSE_IMAGE}" in installer
    assert installer.count(TYPESENSE_IMAGE) == 2
    assert TYPESENSE_IMAGE in workflow
    mutable_assignment = re.search(
        r"^TYPESENSE_IMAGE=typesense/typesense:[^@\n]+$", installer, re.MULTILINE
    )
    assert mutable_assignment is None
    assert "            typesense/typesense:27.1 \\" not in workflow
