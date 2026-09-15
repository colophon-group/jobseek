from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.shared import constants


@pytest.fixture(autouse=True)
def _clear_explicit_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "_repo_root", None)


def _configure_source_checkout(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    module_file = root / "src" / "shared" / "constants.py"
    module_file.parent.mkdir(parents=True)
    module_file.touch()
    (root / "pyproject.toml").touch()
    data_dir = root / "data"
    data_dir.mkdir()

    monkeypatch.setattr(constants, "_MODULE_FILE", module_file.resolve())
    monkeypatch.setattr(constants, "_CHECKOUT_CRAWLER_ROOT", root)
    monkeypatch.setattr(constants, "DATA_DIR", data_dir)
    return data_dir


def test_get_data_dir_preserves_explicit_workspace_pivot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "worktree"
    monkeypatch.setattr(constants, "_repo_root", repo_root)

    assert constants.get_data_dir() == repo_root / "apps" / "crawler" / "data"


def test_get_data_dir_uses_structurally_verified_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _configure_source_checkout(monkeypatch, tmp_path / "crawler")
    monkeypatch.setattr(constants, "_INSTALLED_DATA_DIR", tmp_path / "missing-app-data")

    assert constants.get_data_dir() == data_dir


def test_get_data_dir_uses_only_app_data_for_installed_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed_root = tmp_path / "site-packages"
    wheel_data = installed_root / "data"
    wheel_data.mkdir(parents=True)
    module_file = installed_root / "src" / "shared" / "constants.py"
    module_file.parent.mkdir(parents=True)
    module_file.touch()
    staged_data = tmp_path / "app-data"
    staged_data.mkdir()

    monkeypatch.setattr(constants, "_MODULE_FILE", module_file.resolve())
    monkeypatch.setattr(constants, "_CHECKOUT_CRAWLER_ROOT", installed_root)
    monkeypatch.setattr(constants, "DATA_DIR", wheel_data)
    monkeypatch.setattr(constants, "_INSTALLED_DATA_DIR", staged_data)

    assert constants.get_data_dir() == staged_data


def test_get_data_dir_fails_closed_without_installed_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed_root = tmp_path / "site-packages"
    wheel_data = installed_root / "data"
    wheel_data.mkdir(parents=True)
    (wheel_data / "companies.csv").touch()
    copied_source_data = tmp_path / "app" / "src" / "data"
    copied_source_data.mkdir(parents=True)
    (copied_source_data / "companies.csv").touch()

    monkeypatch.setattr(constants, "_MODULE_FILE", installed_root / "src/shared/constants.py")
    monkeypatch.setattr(constants, "_CHECKOUT_CRAWLER_ROOT", installed_root)
    monkeypatch.setattr(constants, "DATA_DIR", wheel_data)
    monkeypatch.setattr(constants, "_INSTALLED_DATA_DIR", tmp_path / "missing-app-data")

    with pytest.raises(
        RuntimeError,
        match=r"installed crawler runtime requires the /app/data directory",
    ):
        constants.get_data_dir()


def test_runtime_modules_cannot_import_static_data_dir() -> None:
    """Keep every runtime caller on the wheel-safe dynamic resolver."""

    source_root = Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "src.shared.constants":
                continue
            if any(alias.name == "DATA_DIR" for alias in node.names):
                offenders.append(str(path.relative_to(source_root)))

    assert offenders == []
