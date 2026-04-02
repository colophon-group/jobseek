from __future__ import annotations

import typesense

from src.config import settings

_client: typesense.Client | None = None


def get_typesense_client() -> typesense.Client | None:
    """Return a shared Typesense client, or None if not configured.

    The client is created lazily on first call and reused thereafter.
    Returns None when ``typesense_admin_key`` is empty (feature disabled).
    """
    global _client
    if _client is None and settings.typesense_admin_key:
        _client = typesense.Client(
            {
                "nodes": [
                    {
                        "host": settings.typesense_host,
                        "port": str(settings.typesense_port),
                        "protocol": settings.typesense_protocol,
                    }
                ],
                "api_key": settings.typesense_admin_key,
                "connection_timeout_seconds": 5,
            }
        )
    return _client
