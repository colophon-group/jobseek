from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import socket
import socketserver
import struct
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.request import urlopen

import pytest

from src.metrics import (
    _QuietThreadingWSGIServer,
    _start_metrics_http_server,
    start_metrics_server,
)


def _reset_connection(port: int) -> None:
    sock = socket.create_connection(("127.0.0.1", port))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def test_metrics_listener_defaults_to_loopback() -> None:
    server, _thread = _start_metrics_http_server(0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.shutdown()
        server.server_close()


def test_metrics_server_starts_process_tree_sampler() -> None:
    with (
        patch("src.metrics._start_process_tree_sampler") as start_sampler,
        patch("src.metrics._start_metrics_http_server") as start_http,
    ):
        start_metrics_server(9123)

    start_sampler.assert_called_once_with()
    start_http.assert_called_once_with(9123)


@pytest.mark.parametrize("command", ["run", "run-browser", "export", "drain"])
def test_invalid_installed_version_stops_roles_before_external_clients(command: str) -> None:
    from src import cli

    create_pool = AsyncMock()
    create_http = MagicMock()
    close_pools = AsyncMock()
    with (
        patch.object(cli, "parse_args", return_value=argparse.Namespace(command=command)),
        patch.object(cli, "setup_logging"),
        patch.object(cli, "log"),
        patch.object(cli, "create_local_pool", new=create_pool),
        patch.object(cli, "create_http_client", new=create_http),
        patch.object(cli, "close_all_pools", new=close_pools),
        patch("src.metrics.is_source_checkout", return_value=False),
        patch("src.metrics.distribution_version", return_value="unknown"),
        pytest.raises(RuntimeError, match="distribution version is invalid"),
    ):
        asyncio.run(cli.run())

    create_pool.assert_not_awaited()
    create_http.assert_not_called()
    close_pools.assert_awaited_once_with()


def test_metrics_module_import_does_not_require_runtime_cost_package() -> None:
    script = """
import builtins

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.startswith("src.runtime_cost"):
        raise ModuleNotFoundError(name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import src.metrics
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_metrics_client_reset_does_not_emit_traceback():
    """A peer reset before the request line is expected scrape noise (#5354)."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        server, _thread = _start_metrics_http_server(0, addr="127.0.0.1")
        port = server.server_address[1]
        try:
            for _ in range(5):
                _reset_connection(port)

            # A completed request proves the earlier threaded handlers had a
            # scheduling opportunity and that the listener remains healthy.
            with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
                assert response.status == 200
                assert b"crawler_build_info" in response.read()
            time.sleep(0.05)
        finally:
            server.shutdown()
            server.server_close()

    assert stderr.getvalue() == ""


@pytest.mark.parametrize("error", [ConnectionResetError(), BrokenPipeError()])
def test_expected_disconnects_skip_default_handler(error: OSError):
    server = object.__new__(_QuietThreadingWSGIServer)
    with patch.object(socketserver.BaseServer, "handle_error") as default_handler:
        try:
            raise error
        except OSError:
            server.handle_error(object(), ("127.0.0.1", 1))
    default_handler.assert_not_called()


def test_unexpected_handler_error_keeps_default_traceback_path():
    server = object.__new__(_QuietThreadingWSGIServer)
    request = object()
    address = ("127.0.0.1", 1)
    with patch.object(socketserver.BaseServer, "handle_error") as default_handler:
        try:
            raise RuntimeError("unexpected")
        except RuntimeError:
            server.handle_error(request, address)
    default_handler.assert_called_once_with(request, address)
