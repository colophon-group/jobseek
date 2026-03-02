from __future__ import annotations

from src.shared.logging import setup_logging


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
