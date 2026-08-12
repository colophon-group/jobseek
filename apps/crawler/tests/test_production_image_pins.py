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
    assert '"ghcr.io/${owner}/jobseek-crawler@${{ steps.build-slim.outputs.digest }}"' in workflow
    assert (
        '"ghcr.io/${owner}/jobseek-crawler-browser@'
        '${{ steps.build-browser.outputs.digest }}"' in workflow
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
    assert "SHIM_IMAGE_REF:?SHIM_IMAGE_REF must be an immutable GHCR digest" in compose
    assert "SHIM_IMAGE_TAG:-latest" not in compose
    assert "resolve_shim_image_ref" in deploy
    assert "active deploy environment must contain exactly one SHIM_IMAGE_REF" in deploy
    assert "live environment and Murmur container do not attest one immutable image" in deploy
    assert "jobseek-murmur-shim@sha256:[0-9a-f]{64}" in deploy
    assert "SHIM_IMAGE_REF=${SHIM_IMAGE_REF}" in deploy
    assert "verify_shim_deploy_contract" in deploy
    assert "Murmur live environment, container, and success marker disagree" in deploy
    assert deploy.index("resolve_shim_image_ref", deploy.index("activate_staged_deploy_specs")) < (
        deploy.index('cat > "$ENV_FILE"')
    )
    assert "id: murmur" in workflow
    assert "steps.murmur.outputs.image_ref" in workflow
    assert "BROWSER_IMAGE_REF,SHIM_IMAGE_REF,JOBSEEK_DEPLOY_REVISION" in workflow
    assert 'gh run download "$murmur_run_id"' in workflow
    assert 'revision_ref="${repository}@${release_digest}"' in workflow
    assert 'docker buildx imagetools inspect "$revision_ref"' in workflow
    assert 'keys == ["SLSA"]' in workflow
    assert "Murmur provenance does not attest the exact source revision" in workflow
    assert "zero or multiple linux/amd64 images" in workflow
    assert "zero or multiple SLSA provenance attestations" in workflow


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
    assert "murmur-rollback-override" in workflow
    assert "resolve_running_digest" in workflow
    assert "jobseek-crawler-mutation.lock" in workflow
    assert "flock -w 7200 9" in workflow
    assert "CRAWLER_IMAGE_REF=" in workflow
    assert "BROWSER_IMAGE_REF=" in workflow
    assert "previous_compose=" in workflow
    assert "previous_env=" in workflow
    assert 'mv -f "$previous_compose" "$live_compose"' in workflow
    assert 'mv -f "$previous_env" "$live_env"' in workflow
    assert "active_release=/home/deploy/.crawler-active-release" in workflow
    assert 'verify_release_generation "$active_generation"' in workflow
    assert "rollback-images.override.yml" in workflow
    assert "IMAGE_OVERRIDE_SHA256=$image_override_digest" in workflow
    assert "RELEASE_FORMAT_VERSION=2" in workflow
    assert "BOOTSTRAP_LEGACY=1" in workflow
    assert "BOOTSTRAP_LEGACY=0" in workflow
    assert "config --images" in workflow
    assert "MURMUR_TOKEN=%s" in workflow
    assert "LOCAL_DATABASE_URL=%s" in workflow
    assert 'ROLLBACK_ACTIVE_IMAGE_OVERRIDE="$ACTIVE_IMAGE_OVERRIDE"' in (
        ROOT / "apps/crawler/deploy.sh"
    ).read_text(encoding="utf-8")
    assert "target: /home/deploy/incoming-shim/" in workflow
    assert 'test ! -L "$live_env"' in workflow
    assert "CRAWLER_IMAGE_REF|BROWSER_IMAGE_REF|SHIM_IMAGE_REF" in workflow
    assert "SHIM_IMAGE_REF=%s" in workflow
    assert "GHCR_PULL_TOKEN: ${{ secrets.HETZNER_GH_TOKEN }}" in workflow
    assert "GHCR_PAT:" not in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" not in workflow
    assert "GHCR_PULL_TOKEN,MURMUR_TOKEN,DATABASE_URL" not in workflow
    assert "export MURMUR_TOKEN DATABASE_URL" not in workflow
    rollback = workflow[
        workflow.index("rollback_shim() {") : workflow.index("trap rollback_shim EXIT")
    ]
    restore_compose = rollback.index('mv -f "$previous_compose" "$live_compose"')
    restore_env = rollback.index('mv -f "$previous_env" "$live_env"')
    clean_restart = rollback.index("env -i \\")
    assert restore_compose < clean_restart
    assert restore_env < clean_restart
    assert "previous_compose_sha256" in rollback
    assert "previous_env_sha256" in rollback
    assert 'docker compose --env-file "$live_env"' in rollback
    image_baseline = rollback.index('rollback_compose_args=(-f "$live_compose")')
    bootstrap_images = rollback.index('rollback_compose_args+=(-f "$active_image_override")')
    shim_identity = rollback.index('rollback_compose_args+=(-f "$rollback_override")')
    clean_restart = rollback.index("env -i \\", shim_identity)
    assert image_baseline < bootstrap_images < shim_identity < clean_restart
    transaction = workflow[
        workflow.index("trap rollback_shim EXIT") : workflow.index(
            "rollback_armed=0", workflow.index("curl -sf http://localhost:8080/health")
        )
    ]
    arm = transaction.index("rollback_armed=1")
    env_intent = transaction.index("env_activated=1", arm)
    env_publish = transaction.index('mv "$env_candidate" "$live_env"')
    compose_intent = transaction.index("compose_activated=1", env_publish)
    compose_publish = transaction.index('mv "$compose_candidate" "$live_compose"')
    login = transaction.index("docker login ghcr.io")
    pull = transaction.index("docker compose pull murmur-shim")
    up = transaction.index("docker compose up -d murmur-shim")
    identity = transaction.index("docker inspect deploy-murmur-shim-1")
    assert (
        arm
        < env_intent
        < env_publish
        < compose_intent
        < compose_publish
        < login
        < pull
        < up
        < identity
    )
    assert 'compose_candidate="$(mktemp "${live_compose}.shim.XXXXXX")"' in transaction
    assert "os.fsync(directory_fd)" in transaction
    assert "trap 'exit 129' HUP" in transaction
    assert "trap 'exit 130' INT" in transaction
    assert "trap 'exit 143' TERM" in transaction
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


def test_production_build_inputs_are_digest_pinned() -> None:
    crawler = (ROOT / "apps/crawler/Dockerfile").read_text(encoding="utf-8")
    shim = (ROOT / "apps/murmur-shim/Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.13.15-slim-trixie@sha256:" in crawler
    assert "ghcr.io/astral-sh/uv:0.12.3@sha256:" in crawler
    assert "ghcr.io/astral-sh/uv:latest" not in crawler
    assert "ARG NODE_IMAGE=node:22.23.2-trixie-slim@sha256:" in shim
