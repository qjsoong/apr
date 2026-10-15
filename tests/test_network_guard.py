"""The suite must be unable to reach the network, and the guard must be live.

`tests/conftest.py` installs the guard for every test; these tests exist so that
a future change cannot quietly remove it. Without them, the guard is only
observable through the absence of a failure, which is exactly how it would break
unnoticed.
"""

from __future__ import annotations

import socket

import pytest

GUARD = "test tried to open a network connection"


def test_the_guard_blocks_name_resolution() -> None:
    with pytest.raises(RuntimeError, match=GUARD):
        socket.getaddrinfo("example.org", 80)


def test_the_guard_blocks_connection_attempts() -> None:
    with pytest.raises(RuntimeError, match=GUARD):
        socket.create_connection(("example.org", 80), timeout=0.01)


def test_the_guard_blocks_the_low_level_socket_call() -> None:
    with (
        pytest.raises(RuntimeError, match=GUARD),
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe,
    ):
        probe.connect(("example.org", 80))
