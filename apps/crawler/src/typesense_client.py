from __future__ import annotations

import typesense

from src.config import settings

_clients: dict[int, typesense.Client] = {}


def get_typesense_client(*, num_retries: int = 3) -> typesense.Client | None:
    """Return a shared Typesense client, or None if not configured.

    The client is created lazily per retry policy and reused thereafter.
    Exporter requests use ``num_retries=0`` because its cross-tick outage
    circuit owns retry timing; maintenance and sync callers retain the client
    default for their finite, operator-facing commands.
    Returns None when ``typesense_operations_key`` is empty (feature disabled).
    """
    if not settings.typesense_operations_key:
        return None
    if num_retries < 0:
        raise ValueError("num_retries must be non-negative")
    if num_retries not in _clients:
        _clients[num_retries] = typesense.Client(
            {
                "nodes": [
                    {
                        "host": settings.typesense_host,
                        "port": str(settings.typesense_port),
                        "protocol": settings.typesense_protocol,
                    }
                ],
                "api_key": settings.typesense_operations_key,
                "connection_timeout_seconds": 5,
                "num_retries": num_retries,
            }
        )
    return _clients[num_retries]
