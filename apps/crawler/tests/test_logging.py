from __future__ import annotations

import pytest
import structlog

from src.shared.logging import setup_logging


@pytest.fixture(autouse=True)
def restore_structlog_config():
    """Keep setup_logging's process-global configuration inside each test."""
    previous_config = structlog.get_config()
    previous_config = {
        **previous_config,
        "processors": list(previous_config["processors"]),
    }
    yield
    structlog.configure(**previous_config)


class TestSetupLogging:
    def test_info_level(self):
        setup_logging("INFO")

    def test_debug_level(self):
        setup_logging("DEBUG")

    def test_warning_level(self):
        setup_logging("WARNING")

    def test_case_insensitive(self):
        setup_logging("info")
        setup_logging("Info")
