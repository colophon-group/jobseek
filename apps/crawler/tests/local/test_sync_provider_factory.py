from __future__ import annotations

import pytest

from src.core.enrich.providers import create_sync_provider


def test_create_sync_provider_requires_api_key():
    with pytest.raises(ValueError, match="ENRICH_API_KEY is required"):
        create_sync_provider("gemini", "gemini-2.0-flash", "")
