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


def test_crawler_services_require_immutable_production_images() -> None:
    compose = (ROOT / "apps/crawler/docker-compose.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "apps/crawler/deploy.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy-crawler-browser.yml").read_text(encoding="utf-8")

    assert f"image: {REDIS_IMAGE}" in compose
    assert f'REDIS_IMAGE="{REDIS_IMAGE}"' in deploy
    assert "docker pull redis:8-alpine" not in deploy
    assert "CRAWLER_IMAGE_REF must be an immutable GHCR digest" in compose
    assert "BROWSER_IMAGE_REF must be an immutable GHCR digest" in compose
    assert "CRAWLER_IMAGE_TAG:-latest" not in compose
    assert "id: build-slim" in workflow
    assert "id: build-browser" in workflow
    assert (
        "CRAWLER_IMAGE_REF: ghcr.io/${{ github.repository_owner }}/jobseek-crawler@"
        "${{ needs.build.outputs.slim_digest }}" in workflow
    )
    assert (
        "BROWSER_IMAGE_REF: ghcr.io/${{ github.repository_owner }}/"
        "jobseek-crawler-browser@${{ needs.build.outputs.browser_digest }}" in workflow
    )
    assert "IMAGE: ghcr.io/${{ github.repository_owner }}/jobseek-crawler@" in workflow
    assert '"ghcr.io/${owner}/jobseek-crawler@${{ needs.build.outputs.slim_digest }}"' in workflow
    assert (
        '"ghcr.io/${owner}/jobseek-crawler-browser@'
        '${{ needs.build.outputs.browser_digest }}"' in workflow
    )
    promote = workflow[workflow.index("- name: Promote deployed images to latest") :]
    assert "jobseek-crawler:${version}" not in promote
    assert "jobseek-crawler-browser:${version}" not in promote
    assert "CRAWLER_IMAGE_TAG must be a versioned release/build tag" in deploy
    assert "CRAWLER_IMAGE_REF must be an immutable crawler digest" in deploy
    assert "BROWSER_IMAGE_REF must be an immutable crawler-browser digest" in deploy
    assert '"$CRAWLER_IMAGE_REF"' in deploy
    assert "verify_deployed_image_identity" in deploy
    assert "CRAWLER_IMAGE_REF=$CRAWLER_IMAGE_REF" in deploy
    assert "BROWSER_IMAGE_REF=$BROWSER_IMAGE_REF" in deploy
    assert "REDIS_IMAGE_REF=$REDIS_IMAGE" in deploy


def test_paused_murmur_has_no_deployment_entrypoint() -> None:
    compose = (ROOT / "apps/crawler/docker-compose.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "apps/crawler/deploy.sh").read_text(encoding="utf-8")
    deploy_helpers = (ROOT / "apps/crawler/deploy_helpers.sh").read_text(encoding="utf-8")
    rollback_override = (ROOT / "apps/crawler/rollback-pool-budget.override.yml").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github/workflows/deploy-crawler-browser.yml").read_text(encoding="utf-8")

    assert not (ROOT / ".github/workflows/deploy-murmur-shim.yml").exists()
    assert "murmur-shim" not in compose
    assert "murmur-shim" not in deploy_helpers
    assert "murmur-shim" not in rollback_override
    assert "MURMUR_TOKEN" not in deploy
    assert "SHIM_IMAGE_REF" not in deploy
    assert "murmur" not in workflow.lower()
    compose_commands = (
        line
        for line in deploy.splitlines()
        if line.strip().startswith(("docker compose", "rollback_compose"))
    )
    assert all("--remove-orphans" not in command for command in compose_commands)


def test_typesense_host_and_smoke_use_the_same_manifest_digest() -> None:
    installer = (ROOT / "deploy/typesense-host/install-host.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy-typesense-host.yml").read_text(encoding="utf-8")

    assert f"TYPESENSE_IMAGE={TYPESENSE_IMAGE}" in installer
    crawler_workflow = (ROOT / ".github/workflows/deploy-crawler-browser.yml").read_text(
        encoding="utf-8"
    )

    assert installer.count(TYPESENSE_IMAGE) == 1
    assert TYPESENSE_IMAGE in workflow
    assert TYPESENSE_IMAGE in crawler_workflow
    assert 'container["Config"].get("Image") == expected_image' in installer
    mutable_assignment = re.search(
        r"^TYPESENSE_IMAGE=typesense/typesense:[^@\n]+$", installer, re.MULTILINE
    )
    assert mutable_assignment is None
    assert "            typesense/typesense:27.1 \\" not in workflow


def test_production_build_inputs_are_digest_pinned() -> None:
    crawler = (ROOT / "apps/crawler/Dockerfile").read_text(encoding="utf-8")
    shim = (ROOT / "apps/murmur-shim/Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.13.15-slim-trixie@sha256:" in crawler
    assert "ghcr.io/astral-sh/uv:0.12.3@sha256:" in crawler
    assert "ghcr.io/astral-sh/uv:latest" not in crawler
    assert "ARG NODE_IMAGE=node:22.23.2-trixie-slim@sha256:" in shim
