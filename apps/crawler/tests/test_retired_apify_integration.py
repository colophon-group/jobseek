"""Regression guard for the retired Apify vendor integration."""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_ROOTS = (REPO_ROOT,)
EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "__tests__",
    "dist",
    "fixtures",
    "node_modules",
    "tests",
}
EXCLUDED_PREFIXES = (
    Path("apps/crawler/data"),
    Path("apps/crawler/murmur"),
    Path("apps/murmur-shim"),
    Path("apps/web/app/api/admin/murmur-demo"),
)
EXCLUDED_FILES = {
    Path("packages/mcp-server/scripts/verify-package.mjs"),
}
SOURCE_AND_CONFIG_SUFFIXES = {
    ".cjs",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".mjs",
    ".mts",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
SOURCE_AND_CONFIG_NAMES = {"Dockerfile", "Procfile"}
RETIRED_VENDOR_MARKERS = re.compile(
    r"\bAPIFY_TOKEN\b"
    r"|api\.apify\.com(?:/v2)?"
    r"|[\"']apify-client[\"']"
    r"|\btrigger_discovery_run\b"
    r"|\bget_discovery_results\b"
    r"|/agentic/api/discovery"
    r"|\bapify-(?:actor|dataset)\b",
    re.IGNORECASE,
)


def _is_excluded(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT)
    return relative in EXCLUDED_FILES or any(
        relative == prefix or prefix in relative.parents for prefix in EXCLUDED_PREFIXES
    )


def _scanned_files() -> list[Path]:
    files: list[Path] = []

    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(root):
            current = Path(directory)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXCLUDED_DIRECTORIES and not _is_excluded(current / name)
            )
            if _is_excluded(current):
                dirnames[:] = []
                continue
            for name in sorted(filenames):
                path = current / name
                if (
                    not name.startswith(".env")
                    and (
                        path.suffix in SOURCE_AND_CONFIG_SUFFIXES or name in SOURCE_AND_CONFIG_NAMES
                    )
                    and not _is_excluded(path)
                ):
                    files.append(path)

    return files


def test_retired_apify_vendor_integration_stays_out_of_runtime_surfaces() -> None:
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _scanned_files()
        if RETIRED_VENDOR_MARKERS.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_obsolete_bulk_importer_stays_deleted() -> None:
    assert not (REPO_ROOT / "scripts/bulk_company_requests.py").exists()


def _csv_row(path: Path, key: str, value: str) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as file:
        return next(row for row in csv.DictReader(file) if row[key] == value)


def test_apify_company_and_generic_career_board_remain_tracked() -> None:
    data_root = REPO_ROOT / "apps/crawler/data"
    company = _csv_row(data_root / "companies.csv", "slug", "apify")
    description = _csv_row(data_root / "company_descriptions.csv", "slug", "apify")
    board = _csv_row(data_root / "boards.csv", "board_slug", "apify-careers")

    assert company["name"] == "Apify"
    assert description["en"]
    assert board["company_slug"] == "apify"
    assert board["board_url"] == "https://apify.com/jobs"
    assert board["monitor_type"] == "ashby"
    assert json.loads(board["monitor_config"]) == {"token": "apify"}
