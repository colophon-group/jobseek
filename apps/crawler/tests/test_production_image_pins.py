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
        "${{ steps.build-slim.outputs.digest }}" in workflow
    )
    assert (
        "BROWSER_IMAGE_REF: ghcr.io/${{ github.repository_owner }}/"
        "jobseek-crawler-browser@${{ steps.build-browser.outputs.digest }}" in workflow
    )
    assert "IMAGE: ghcr.io/${{ github.repository_owner }}/jobseek-crawler@" in workflow
    assert "CRAWLER_IMAGE_TAG must be a versioned release/build tag" in deploy
    assert "CRAWLER_IMAGE_REF must be an immutable crawler digest" in deploy
    assert "BROWSER_IMAGE_REF must be an immutable crawler-browser digest" in deploy
    assert '"$CRAWLER_IMAGE_REF"' in deploy
    assert "verify_deployed_image_identity" in deploy
    assert "CRAWLER_IMAGE_REF=$CRAWLER_IMAGE_REF" in deploy
    assert "BROWSER_IMAGE_REF=$BROWSER_IMAGE_REF" in deploy
    assert "REDIS_IMAGE_REF=$REDIS_IMAGE" in deploy
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
    assert "resolve_running_digest" in workflow
    assert "CRAWLER_IMAGE_REF=" in workflow
    assert "BROWSER_IMAGE_REF=" in workflow
    assert "previous_compose=" in workflow
    assert "previous_env=" in workflow
    assert 'mv -f "$previous_compose" "$live_compose"' in workflow
    assert 'mv -f "$previous_env" /home/deploy/.env' in workflow
    assert "target: /home/deploy/incoming-shim/" in workflow
    assert "test ! -L /home/deploy/.env" in workflow
    assert "CRAWLER_IMAGE_REF|BROWSER_IMAGE_REF|SHIM_IMAGE_REF" in workflow
    assert "SHIM_IMAGE_REF=%s" in workflow
    assert "GHCR_PULL_TOKEN: ${{ secrets.HETZNER_GH_TOKEN }}" in workflow
    assert "GHCR_PAT:" not in workflow
    assert "needs: [build, deploy]" in workflow
    assert "jobseek-murmur-shim:latest" in workflow  # Post-deploy compatibility tag only.
    build_job = workflow[workflow.index("  build:") : workflow.index("\n  deploy:")]
    assert "jobseek-murmur-shim:latest" not in build_job
    deploy_script = workflow[workflow.index("script: |") :]
    deploy_script = deploy_script[: deploy_script.index("\n        env:")]
    assert "jobseek-murmur-shim:latest" not in deploy_script


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
