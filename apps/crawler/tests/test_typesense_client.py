from __future__ import annotations

from unittest.mock import patch

import pytest

from src import typesense_client


@pytest.fixture(autouse=True)
def clear_clients():
    typesense_client._clients.clear()
    yield
    typesense_client._clients.clear()


def test_clients_are_cached_per_retry_policy():
    with (
        patch.object(typesense_client.settings, "typesense_operations_key", "test-key"),
        patch.object(typesense_client.settings, "typesense_host", "127.0.0.1"),
        patch.object(
            typesense_client.typesense,
            "Client",
            side_effect=[object(), object()],
        ) as client_factory,
    ):
        exporter_client = typesense_client.get_typesense_client(num_retries=0)
        assert typesense_client.get_typesense_client(num_retries=0) is exporter_client
        maintenance_client = typesense_client.get_typesense_client()

    assert maintenance_client is not exporter_client
    assert client_factory.call_count == 2
    assert client_factory.call_args_list[0].args[0]["num_retries"] == 0
    assert client_factory.call_args_list[1].args[0]["num_retries"] == 3


def test_negative_retries_are_rejected():
    with (
        patch.object(typesense_client.settings, "typesense_operations_key", "test-key"),
        pytest.raises(ValueError, match="non-negative"),
    ):
        typesense_client.get_typesense_client(num_retries=-1)
