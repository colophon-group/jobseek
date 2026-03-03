"""Shared constants used across csvtool, inspect, and workspace."""

from __future__ import annotations

import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
WORKSPACE_DIR = Path(__file__).parent.parent.parent / ".workspace"

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
URL_RE = re.compile(r"^https?://[^\s/]+")
