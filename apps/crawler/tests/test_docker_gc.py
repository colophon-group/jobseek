"""Regression tests for digest-only Docker image retention."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "jobseek-docker-gc.py"
SPEC = importlib.util.spec_from_file_location("jobseek_docker_gc", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gc
SPEC.loader.exec_module(gc)


def _image(value: str) -> str:
    return f"sha256:{value * 64}"


def test_repository_retention_preserves_container_and_known_good_rollbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = "ghcr.io/colophon-group/jobseek-crawler"
    oldest, rollback_one, active, rollback_two, failed_newest = (_image(value) for value in "abcde")
    monkeypatch.setattr(
        gc,
        "_repository_images",
        lambda _repository: [
            gc.Image(oldest, "2026-09-14T01:00:00Z"),
            gc.Image(rollback_one, "2026-09-14T02:00:00Z"),
            gc.Image(active, "2026-09-14T03:00:00Z"),
            gc.Image(rollback_two, "2026-09-14T04:00:00Z"),
            gc.Image(failed_newest, "2026-09-14T05:00:00Z"),
        ],
    )

    protected, removable = gc._repository_retention(
        repository,
        container_images={active},
        rollback_images={rollback_one, rollback_two},
    )

    assert protected == {active, rollback_one, rollback_two}
    assert removable == {oldest, failed_newest}


def test_repository_retention_protects_active_release_during_container_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active, rollback, failed = (_image(value) for value in "abc")
    monkeypatch.setattr(
        gc,
        "_repository_images",
        lambda _repository: [
            gc.Image(active, "2026-09-12T00:00:00Z"),
            gc.Image(rollback, "2026-09-11T00:00:00Z"),
            gc.Image(failed, "2026-09-14T00:00:00Z"),
        ],
    )

    protected, removable = gc._repository_retention(
        gc.CRAWLER_REPOSITORIES[0],
        container_images=set(),
        rollback_images={active, rollback},
    )

    assert protected == {active, rollback}
    assert removable == {failed}


def _write_release(path: Path, *, crawler: str, browser: str) -> None:
    path.mkdir()
    success = (
        f"CRAWLER_IMAGE_REF={gc.CRAWLER_REPOSITORIES[0]}@sha256:{crawler * 64}\n"
        f"BROWSER_IMAGE_REF={gc.CRAWLER_REPOSITORIES[1]}@sha256:{browser * 64}\n"
    )
    (path / "success.env").write_text(success, encoding="utf-8")
    digest = hashlib.sha256(success.encode()).hexdigest()
    (path / "release.manifest").write_text(
        f"RELEASE_FORMAT_VERSION=3\nSUCCESS_SHA256={digest}\n",
        encoding="utf-8",
    )


def test_rollback_history_uses_verified_published_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".crawler-release-generations"
    root.mkdir()
    old = root / "release-old.aaaa"
    previous = root / "release-previous.bbbb"
    active = root / "release-active.cccc"
    _write_release(old, crawler="a", browser="b")
    _write_release(previous, crawler="c", browser="d")
    _write_release(active, crawler="e", browser="f")
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(previous, ns=(2_000_000_000, 2_000_000_000))
    os.utime(active, ns=(3_000_000_000, 3_000_000_000))
    pointer = tmp_path / ".crawler-active-release"
    pointer.symlink_to(active)
    monkeypatch.setattr(gc, "ACTIVE_RELEASE_ROOT", root)
    monkeypatch.setattr(gc, "ACTIVE_RELEASE_POINTER", pointer)

    active_refs, history = gc._release_ref_history()

    assert active_refs[gc.CRAWLER_REPOSITORIES[0]].endswith("e" * 64)
    assert history[gc.CRAWLER_REPOSITORIES[0]] == [
        f"{gc.CRAWLER_REPOSITORIES[0]}@sha256:{'c' * 64}",
        f"{gc.CRAWLER_REPOSITORIES[0]}@sha256:{'a' * 64}",
    ]
    assert all("e" * 64 not in ref for refs in history.values() for ref in refs)


def test_release_history_skips_partial_nonactive_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".crawler-release-generations"
    root.mkdir()
    active = root / "release-active.aaaa"
    previous = root / "release-previous.bbbb"
    partial = root / "release-partial.cccc"
    _write_release(active, crawler="a", browser="b")
    _write_release(previous, crawler="c", browser="d")
    partial.mkdir()
    (partial / "success.env").write_text("partial\n", encoding="utf-8")
    pointer = tmp_path / ".crawler-active-release"
    pointer.symlink_to(active)
    monkeypatch.setattr(gc, "ACTIVE_RELEASE_ROOT", root)
    monkeypatch.setattr(gc, "ACTIVE_RELEASE_POINTER", pointer)

    _active, history = gc._release_ref_history()

    assert history[gc.CRAWLER_REPOSITORIES[0]] == [
        f"{gc.CRAWLER_REPOSITORIES[0]}@sha256:{'c' * 64}"
    ]


def test_release_history_rejects_partial_active_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".crawler-release-generations"
    root.mkdir()
    active = root / "release-active.aaaa"
    active.mkdir()
    (active / "success.env").write_text("partial\n", encoding="utf-8")
    pointer = tmp_path / ".crawler-active-release"
    pointer.symlink_to(active)
    monkeypatch.setattr(gc, "ACTIVE_RELEASE_ROOT", root)
    monkeypatch.setattr(gc, "ACTIVE_RELEASE_POINTER", pointer)

    with pytest.raises(gc.GarbageCollectionError, match="release evidence"):
        gc._release_ref_history()


def test_known_good_rollbacks_fail_closed_on_missing_local_reference() -> None:
    with pytest.raises(gc.GarbageCollectionError, match="not locally available"):
        gc._known_good_rollbacks(
            ["missing-ref"],
            reference_index={},
            excluded_images=set(),
            count=2,
        )


def test_known_good_rollbacks_retain_configured_distinct_count() -> None:
    refs = ["release-one", "release-two", "release-three"]
    index = {ref: _image(value) for ref, value in zip(refs, "abc", strict=True)}

    retained = gc._known_good_rollbacks(
        refs,
        reference_index=index,
        excluded_images=set(),
        count=2,
    )

    assert retained == {_image("a"), _image("b")}


def test_known_good_rollbacks_fail_closed_below_configured_count() -> None:
    with pytest.raises(gc.GarbageCollectionError, match="required verified rollback"):
        gc._known_good_rollbacks(
            ["release-one"],
            reference_index={"release-one": _image("a")},
            excluded_images=set(),
            count=2,
        )


def test_remove_images_uses_immutable_id_and_redacts_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image_id = _image("a")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        gc,
        "_run",
        lambda args: calls.append(args) or subprocess.CompletedProcess(args, 0, ""),
    )

    assert gc._remove_images([gc.Image(image_id, "2026-09-14T01:00:00Z")]) == (1, 0)
    assert calls == [["docker", "image", "rm", image_id]]
    output = capsys.readouterr().out
    assert image_id not in output
    assert image_id.removeprefix("sha256:")[:12] not in output
    assert "image_generation=" in output


def test_crawler_plan_removes_failed_candidate_but_not_unrelated_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active, active_browser, rollback_one, rollback_two, failed, unrelated_old, unrelated_new = (
        _image(value) for value in "abcdefg"
    )
    active_ref = f"{gc.CRAWLER_REPOSITORIES[0]}@sha256:{'1' * 64}"
    active_browser_ref = f"{gc.CRAWLER_REPOSITORIES[1]}@sha256:{'2' * 64}"
    repositories = {
        gc.CRAWLER_REPOSITORIES[0]: [
            gc.Image(active, "2026-09-10T00:00:00Z", frozenset({active_ref})),
            gc.Image(rollback_one, "2026-09-11T00:00:00Z"),
            gc.Image(rollback_two, "2026-09-12T00:00:00Z"),
            gc.Image(failed, "2026-09-14T09:00:00Z"),
        ],
        gc.CRAWLER_REPOSITORIES[1]: [
            gc.Image(
                active_browser,
                "2026-09-10T00:00:00Z",
                frozenset({active_browser_ref}),
            )
        ],
    }
    monkeypatch.setattr(gc, "_container_image_ids", lambda: {active})
    monkeypatch.setattr(
        gc,
        "_release_ref_history",
        lambda: (
            {
                gc.CRAWLER_REPOSITORIES[0]: active_ref,
                gc.CRAWLER_REPOSITORIES[1]: active_browser_ref,
            },
            {
                gc.CRAWLER_REPOSITORIES[0]: ["known-good"],
                gc.CRAWLER_REPOSITORIES[1]: [],
            },
        ),
    )
    monkeypatch.setattr(
        gc,
        "_known_good_rollbacks",
        lambda refs, **_kwargs: {rollback_one, rollback_two} if refs else set(),
    )
    monkeypatch.setattr(gc, "_repository_images", lambda repository: repositories[repository])
    monkeypatch.setattr(
        gc,
        "_all_images",
        lambda: [
            *repositories[gc.CRAWLER_REPOSITORIES[0]],
            *repositories[gc.CRAWLER_REPOSITORIES[1]],
            gc.Image(unrelated_old, "2026-09-01T00:00:00Z"),
            gc.Image(unrelated_new, "2026-09-14T08:00:00Z"),
        ],
    )
    removed: list[gc.Image] = []
    monkeypatch.setattr(
        gc,
        "_remove_images",
        lambda images: (removed.extend(images) or len(images), 0),
    )
    config = gc.Config(
        role="crawler",
        min_free_kb=15,
        crawler_keep=2,
        builder_until=None,
    )

    assert gc._prune_crawler_images(config) == (1, 0)
    assert {image.image_id for image in removed} == {failed}


def test_crawler_plan_never_removes_id_retained_by_another_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared, crawler_old, browser_active = (_image(value) for value in "abc")
    crawler_ref = f"{gc.CRAWLER_REPOSITORIES[0]}@sha256:{'1' * 64}"
    browser_ref = f"{gc.CRAWLER_REPOSITORIES[1]}@sha256:{'2' * 64}"
    images = [
        gc.Image(shared, "2026-09-10T00:00:00Z", frozenset({crawler_ref})),
        gc.Image(crawler_old, "2026-09-09T00:00:00Z"),
        gc.Image(browser_active, "2026-09-10T00:00:00Z", frozenset({browser_ref})),
    ]
    monkeypatch.setattr(gc, "_container_image_ids", set)
    monkeypatch.setattr(
        gc,
        "_release_ref_history",
        lambda: (
            {
                gc.CRAWLER_REPOSITORIES[0]: crawler_ref,
                gc.CRAWLER_REPOSITORIES[1]: browser_ref,
            },
            {repository: [] for repository in gc.CRAWLER_REPOSITORIES},
        ),
    )
    monkeypatch.setattr(gc, "_all_images", lambda: images)
    monkeypatch.setattr(gc, "_known_good_rollbacks", lambda *_args, **_kwargs: set())
    plans = iter((({shared}, {crawler_old}), ({browser_active}, {shared})))
    monkeypatch.setattr(gc, "_repository_retention", lambda *_args, **_kwargs: next(plans))
    removed: list[gc.Image] = []
    monkeypatch.setattr(
        gc,
        "_remove_images",
        lambda candidates: (removed.extend(candidates) or len(candidates), 0),
    )
    config = gc.Config(
        role="crawler",
        min_free_kb=15,
        crawler_keep=2,
        builder_until=None,
    )

    assert gc._prune_crawler_images(config) == (1, 0)
    assert [image.image_id for image in removed] == [crawler_old]


def test_crawler_gc_prunes_all_builder_cache_but_never_crosses_retention_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free_values = iter((10, 12))
    calls: list[list[str]] = []

    monkeypatch.setattr(gc, "_free_kb", lambda: next(free_values))
    monkeypatch.setattr(gc, "_prune_crawler_images", lambda _config: (1, 0))

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "")

    monkeypatch.setattr(gc, "_run", fake_run)
    config = gc.Config(
        role="crawler",
        min_free_kb=15,
        crawler_keep=2,
        builder_until=None,
    )

    assert gc.run_gc(config) == 1
    assert calls[0] == ["docker", "builder", "prune", "--all", "--force"]
    assert all(call[:3] != ["docker", "image", "prune"] for call in calls)


def test_data_host_gc_reports_low_floor_without_pruning_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free_values = iter((10, 10))
    calls: list[list[str]] = []
    monkeypatch.setattr(gc, "_free_kb", lambda: next(free_values))
    monkeypatch.setattr(
        gc,
        "_run",
        lambda args: calls.append(args) or subprocess.CompletedProcess(args, 0, ""),
    )
    config = gc.Config(
        role="postgresql",
        min_free_kb=15,
        crawler_keep=2,
        builder_until="24h",
    )

    assert gc.run_gc(config) == 1
    assert calls == [["docker", "builder", "prune", "--all", "--force", "--filter", "until=24h"]]


def test_crawler_mutation_lock_is_created_by_deploy_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "jobseek-crawler-mutation.lock"
    calls: list[list[str]] = []
    account = type("Account", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})()
    monkeypatch.setattr(gc, "CRAWLER_MUTATION_LOCK_PATH", lock_path)
    monkeypatch.setattr(gc.pwd, "getpwnam", lambda _name: account)

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        descriptor = os.open(
            args[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
        return subprocess.CompletedProcess(args, 0, "")

    monkeypatch.setattr(gc, "_run", fake_run)

    with gc._open_crawler_mutation_lock() as lock:
        assert lock.readable()

    assert calls[0][:4] == ["runuser", "-u", "deploy", "--"]
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    source = SCRIPT.read_text(encoding="utf-8")
    assert "CRAWLER_MUTATION_LOCK_PATH.parent.mkdir" not in source
    assert "CRAWLER_MUTATION_LOCK_PATH.open" not in source


def test_crawler_default_floor_precedes_daily_incident_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBSEEK_HOST_ROLE", "crawler")
    for name in (
        "JOBSEEK_DOCKER_GC_MIN_FREE_KB",
        "JOBSEEK_DOCKER_GC_CRAWLER_KEEP",
        "JOBSEEK_DOCKER_GC_BUILDER_UNTIL",
    ):
        monkeypatch.delenv(name, raising=False)

    config = gc._config_from_env()

    assert config.min_free_kb == 15 * gc.GIB_KB
    assert config.builder_until is None
    assert config.crawler_keep == 2
