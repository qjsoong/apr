"""Shared test fixtures.

The autouse ``_no_network`` guard is deliberate: apr is offline by default and
the suite must not quietly start depending on a live service. A test that needs
the network uses ``@pytest.mark.network`` and runs only when
``APR_TEST_ALLOW_NETWORK=1``.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("APR_TEST_ALLOW_NETWORK") == "1":
        return

    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError(
            "test tried to open a network connection; mark it 'network' and set "
            "APR_TEST_ALLOW_NETWORK=1 to allow live requests"
        )

    # Cover the whole in-process path: name resolution as well as the connection
    # itself. Subprocesses are outside this guard, which is why the corpus run is
    # additionally checked inside an empty network namespace.
    monkeypatch.setattr(socket, "getaddrinfo", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("APR_TEST_ALLOW_NETWORK") == "1":
        return
    skip = pytest.mark.skip(reason="needs APR_TEST_ALLOW_NETWORK=1")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def corpus_dir() -> Path:
    """Directory of real PDFs for evaluation; skipped when not configured."""
    raw = os.environ.get("APR_CORPUS")
    if not raw:
        pytest.skip("set APR_CORPUS to a directory of PDFs to run corpus tests")
    path = Path(raw).expanduser()
    if not path.is_dir():
        pytest.skip(f"APR_CORPUS is not a directory: {path}")
    if not any(path.glob("*.pdf")):
        pytest.skip(f"no PDFs in APR_CORPUS: {path}")
    return path
