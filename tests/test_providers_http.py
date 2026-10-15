"""Tests for apr.providers.http: the one module allowed to open a socket.

Every test replaces the opener, so nothing here reaches the network — the guard
in ``tests/conftest.py`` fails the suite if anything tries.
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

import pytest

from apr.providers import http


class FakeOpener:
    """Stands in for :func:`apr.providers.http.open_request`.

    Returns each outcome in turn and keeps returning the last one, so a test can
    say "500 once, then 200" or "always 503".
    """

    def __init__(self, *outcomes: tuple[int, bytes, Mapping[str, str]] | Exception):
        self.outcomes = outcomes
        self.requests: list[urllib.request.Request] = []

    def __call__(
        self, request: urllib.request.Request, timeout: float
    ) -> tuple[int, bytes, Mapping[str, str]]:
        self.requests.append(request)
        outcome = self.outcomes[min(len(self.requests) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def urls(self) -> list[str]:
        return [request.full_url for request in self.requests]


def options_for(
    opener: FakeOpener,
    cache_dir: Path | None = None,
    *,
    timeout: float = 10.0,
    request_limit: int | None = None,
) -> tuple[http.HttpOptions, list[float]]:
    slept: list[float] = []
    options = http.HttpOptions(
        opener=opener,
        cache_dir=cache_dir,
        timeout=timeout,
        request_limit=request_limit,
        sleeper=slept.append,
        host_interval=0.0,
    )
    return options, slept


def http_error(
    code: int, retry_after: str | None = None, url: str = "https://example.test/x"
) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, code, f"HTTP {code}", headers, io.BytesIO(b""))


def test_get_json_parses_the_body_and_identifies_the_tool() -> None:
    opener = FakeOpener((200, b'{"message": {"DOI": "10.1/x"}}', {}))
    options, _ = options_for(opener)

    assert http.get_json("https://example.test/paper", options=options) == {
        "message": {"DOI": "10.1/x"}
    }
    request = opener.requests[0]
    assert request.full_url == "https://example.test/paper"
    assert request.get_method() == "GET"
    assert (
        request.get_header("User-agent")
        == http.USER_AGENT
        == ("apr/0.1.0 (+https://github.com/qjsoong/apr)")
    )


def test_get_text_returns_a_non_json_body_verbatim() -> None:
    opener = FakeOpener((200, b"<feed><entry/></feed>", {}))
    options, _ = options_for(opener)

    assert http.get_text("https://example.test/feed", options=options) == (
        "<feed><entry/></feed>"
    )


def test_the_cache_serves_the_second_call_without_a_request(tmp_path: Path) -> None:
    opener = FakeOpener((200, b'{"ok": true}', {}))
    options, _ = options_for(opener, cache_dir=tmp_path)

    first = http.get_json("https://example.test/paper", options=options)
    second = http.get_json("https://example.test/paper", options=options)

    assert first == second == {"ok": True}
    assert len(opener.requests) == 1
    cached = list(tmp_path.glob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text("utf-8"))["url"] == (
        "https://example.test/paper"
    )
    assert (
        cached[0].name == http.cache_path(tmp_path, "https://example.test/paper").name
    )


def test_two_urls_get_two_cache_entries(tmp_path: Path) -> None:
    opener = FakeOpener((200, b'{"a": 1}', {}), (200, b'{"b": 2}', {}))
    options, _ = options_for(opener, cache_dir=tmp_path)

    http.get_json("https://example.test/a", options=options)
    http.get_json("https://example.test/b", options=options)

    assert len(list(tmp_path.glob("*.json"))) == 2


def test_the_cache_is_disabled_when_cache_dir_is_none() -> None:
    opener = FakeOpener((200, b'{"ok": true}', {}))
    options, _ = options_for(opener, cache_dir=None)

    http.get_json("https://example.test/paper", options=options)
    http.get_json("https://example.test/paper", options=options)

    assert len(opener.requests) == 2


def test_an_unreadable_cache_entry_is_ignored(tmp_path: Path) -> None:
    path = http.cache_path(tmp_path, "https://example.test/paper")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", "utf-8")
    opener = FakeOpener((200, b'{"ok": true}', {}))
    options, _ = options_for(opener, cache_dir=tmp_path)

    assert http.get_json("https://example.test/paper", options=options) == {"ok": True}
    assert len(opener.requests) == 1


def test_a_500_is_retried_and_then_succeeds() -> None:
    opener = FakeOpener((500, b"", {}), (200, b'{"ok": true}', {}))
    options, slept = options_for(opener)

    assert http.get_json("https://example.test/paper", options=options) == {"ok": True}
    assert len(opener.requests) == 2
    assert slept == [http.BACKOFF]


def test_retries_are_bounded_and_report_the_status() -> None:
    opener = FakeOpener((503, b"", {}))
    options, slept = options_for(opener)

    with pytest.raises(http.HttpError) as raised:
        http.get_json("https://example.test/paper", options=options)

    assert len(opener.requests) == http.RETRIES + 1
    assert raised.value.status == 503
    assert raised.value.url == "https://example.test/paper"
    assert not raised.value.not_found
    assert slept == [http.BACKOFF, http.BACKOFF * 2]


def test_a_retry_after_header_is_honoured_up_to_a_bound() -> None:
    asked = FakeOpener(http_error(429, retry_after="2"), (200, b'{"ok": true}', {}))
    options, slept = options_for(asked)
    assert http.get_json("https://example.test/paper", options=options) == {"ok": True}
    assert slept == [2.0]

    endless = FakeOpener(http_error(429, retry_after="600"))
    options, slept = options_for(endless)
    with pytest.raises(http.HttpError):
        http.get_json("https://example.test/paper", options=options)
    assert slept == [http.MAX_RETRY_AFTER] * (http.RETRIES + 1 - 1)


def test_a_404_is_not_retried_and_counts_as_not_found() -> None:
    opener = FakeOpener(http_error(404))
    options, slept = options_for(opener)

    with pytest.raises(http.HttpError) as raised:
        http.get_json("https://example.test/paper", options=options)

    assert len(opener.requests) == 1
    assert raised.value.not_found
    assert raised.value.status == 404
    assert slept == []


def test_a_403_is_not_retried_either() -> None:
    opener = FakeOpener(http_error(403))
    options, _ = options_for(opener)

    with pytest.raises(http.HttpError) as raised:
        http.get_text("https://example.test/feed", options=options)

    assert raised.value.status == 403
    assert len(opener.requests) == 1


def test_a_timeout_is_retried_then_raised() -> None:
    opener = FakeOpener(TimeoutError("timed out"))
    options, slept = options_for(opener, timeout=3.0)

    with pytest.raises(http.HttpError) as raised:
        http.get_json("https://example.test/paper", options=options)

    assert len(opener.requests) == http.RETRIES + 1
    assert raised.value.status is None
    assert "cannot reach" in str(raised.value)
    assert slept == [http.BACKOFF, http.BACKOFF * 2]


def test_a_connection_error_is_wrapped_with_its_reason() -> None:
    opener = FakeOpener(urllib.error.URLError("name or service not known"))
    options, _ = options_for(opener)

    with pytest.raises(http.HttpError) as raised:
        http.get_json("https://example.test/paper", options=options)

    assert "name or service not known" in str(raised.value)


def test_a_body_that_is_not_json_raises_the_same_error_type() -> None:
    opener = FakeOpener((200, b"<html>this is not json</html>", {}))
    options, slept = options_for(opener)

    with pytest.raises(http.HttpError) as raised:
        http.get_json("https://example.test/paper", options=options)

    assert "malformed JSON" in str(raised.value)
    assert "https://example.test/paper" in str(raised.value)
    assert len(opener.requests) == 1  # a parse failure is not a transport failure
    assert slept == []


def test_requests_to_one_host_are_spaced_out() -> None:
    opener = FakeOpener((200, b"{}", {}), (200, b"{}", {}), (200, b"{}", {}))
    slept: list[float] = []
    options = http.HttpOptions(
        opener=opener, sleeper=slept.append, host_interval=http.MIN_HOST_INTERVAL
    )

    http.get_json("https://api.crossref.org/works/1", options=options)
    http.get_json("https://api.crossref.org/works/2", options=options)
    http.get_json("https://api.openalex.org/works/1", options=options)

    assert len(slept) == 1
    assert http.MIN_HOST_INTERVAL - 0.1 <= slept[0] <= http.MIN_HOST_INTERVAL


def test_a_cache_write_that_fails_does_not_fail_the_lookup(tmp_path: Path) -> None:
    """The cache directory may be read-only; the lookup still has to work."""
    blocked = tmp_path / "cache"
    blocked.write_text("a file, not a directory", "utf-8")
    opener = FakeOpener((200, b'{"ok": true}', {}))
    options, _ = options_for(opener, cache_dir=blocked)

    assert http.get_json("https://example.test/paper", options=options) == {"ok": True}


def test_the_request_limit_stops_a_retry() -> None:
    opener = FakeOpener((503, b"", {}))
    options, slept = options_for(opener, request_limit=1)

    with pytest.raises(http.HttpError) as raised:
        http.get_json("https://example.test/paper", options=options)

    assert raised.value.status == 503
    assert len(opener.requests) == 1
    assert options.requests_made == 1
    assert options.out_of_budget
    assert slept == []


def test_the_request_limit_counts_every_round_trip() -> None:
    opener = FakeOpener((500, b"", {}), (200, b'{"ok": true}', {}))
    options, _ = options_for(opener, request_limit=2)

    assert http.get_json("https://example.test/paper", options=options) == {"ok": True}
    assert options.requests_made == 2


def test_a_cached_answer_does_not_spend_the_budget(tmp_path: Path) -> None:
    opener = FakeOpener((200, b'{"ok": true}', {}))
    options, _ = options_for(opener, cache_dir=tmp_path, request_limit=1)

    http.get_json("https://example.test/paper", options=options)
    assert http.get_json("https://example.test/paper", options=options) == {"ok": True}
    assert options.requests_made == 1  # the cached answer cost nothing
    assert len(opener.requests) == 1
