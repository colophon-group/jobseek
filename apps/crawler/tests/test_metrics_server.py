from __future__ import annotations

import contextlib
import io
import socket
import socketserver
import struct
import time
from unittest.mock import patch
from urllib.request import urlopen

import pytest

from src.metrics import _QuietThreadingWSGIServer, _start_metrics_http_server


def _reset_connection(port: int) -> None:
    sock = socket.create_connection(("127.0.0.1", port))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


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
