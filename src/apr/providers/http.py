"""The only module in apr that opens a socket.

One GET per URL, stdlib ``urllib`` only, with three pieces of politeness that
belong together: a named ``User-Agent``, bounded retries on the statuses that
mean "come back later", and a JSON file cache so a second run costs nothing.
Tests replace :data:`open_request` or pass their own ``opener``; nothing else
in the package touches the network.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

USER_AGENT = "apr/0.1.0 (+https://github.com/qjsoong/apr)"

#: Retries after the first attempt, and the backoff before each of them.
RETRIES = 2
BACKOFF = 0.5

#: Longest single wait, so a service that asks for minutes cannot pin us.
MAX_RETRY_AFTER = 5.0

#: Statuses worth retrying: too many requests and the transient server errors.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Shortest gap between two requests to the same host.
MIN_HOST_INTERVAL = 0.5

#: One GET: ``(request, timeout) -> (status, body, headers)``.
Opener = Callable[
    [urllib.request.Request, float], "tuple[int, bytes, Mapping[str, str]]"
]


class HttpError(Exception):
    """A request that could not be completed or answered with a non-2xx status."""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.retry_after = retry_after

    @property
    def not_found(self) -> bool:
        """True when the service says the identifier is unknown, not broken."""
        return self.status in (404, 410)


def open_request(
    request: urllib.request.Request, timeout: float
) -> tuple[int, bytes, Mapping[str, str]]:
    """Perform one GET through urllib. Replaced in tests, never mocked out."""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(), response.headers


@dataclass(slots=True)
class HttpOptions:
    """Per-run HTTP settings handed to every provider lookup.

    ``request_limit`` and ``requests_made`` bound the work one record may cause:
    the counter counts every round trip, retries included, and the last raise
    happens rather than a retry that would exceed the limit. A cache hit costs
    nothing, so a cached run can still try every provider.
    """

    timeout: float = 10.0
    cache_dir: Path | None = None
    retries: int = RETRIES
    request_limit: int | None = None
    requests_made: int = 0
    #: ``None`` resolves to :func:`open_request` at call time, so a test can
    #: replace either this field or the module function.
    opener: Opener | None = None
    sleeper: Callable[[float], None] = time.sleep
    host_interval: float = MIN_HOST_INTERVAL
    _last_host: dict[str, float] = field(default_factory=dict, repr=False)

    @property
    def out_of_budget(self) -> bool:
        """True when one more round trip would break the per-record limit."""
        return (
            self.request_limit is not None and self.requests_made >= self.request_limit
        )

    def wait(self, url: str) -> None:
        """Sleep just enough to keep requests to one host spaced apart."""
        host = urlsplit(url).netloc
        now = time.monotonic()
        last = self._last_host.get(host)
        if last is not None and now - last < self.host_interval:
            self.sleeper(self.host_interval - (now - last))
            now = time.monotonic()
        self._last_host[host] = now


def get_text(url: str, *, options: HttpOptions | None = None) -> str:
    """GET a URL and return its body, read from the cache when possible."""
    options = options or HttpOptions()
    if options.cache_dir is not None:
        cached = _read_cache(options.cache_dir, url)
        if cached is not None:
            return cached
    text = _request_text(url, options)
    if options.cache_dir is not None:
        _write_cache(options.cache_dir, url, text)
    return text


def get_json(url: str, *, options: HttpOptions | None = None) -> Any:
    """GET a URL and parse the body as JSON.

    Raises :class:`HttpError` for a failed request *and* for a body that is not
    JSON, so callers have one failure type to handle.
    """
    text = get_text(url, options=options)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HttpError(f"malformed JSON from {url}: {exc.msg}", url=url) from exc


def cache_path(cache_dir: Path, url: str) -> Path:
    """Where the body of ``url`` is cached: one file per URL."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}.json"


def _request_text(url: str, options: HttpOptions) -> str:
    opener = options.opener or open_request
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}, method="GET"
    )
    retries = max(options.retries, 0)
    for attempt in range(retries + 1):
        options.wait(url)
        options.requests_made += 1
        try:
            status, body, _headers = opener(request, options.timeout)
        except urllib.error.HTTPError as exc:
            error = HttpError(
                f"HTTP {exc.code} from {url}",
                url=url,
                status=exc.code,
                retry_after=exc.headers.get("Retry-After"),
            )
        except urllib.error.URLError as exc:
            error = HttpError(f"cannot reach {url}: {exc.reason}", url=url)
        except (TimeoutError, OSError) as exc:
            error = HttpError(f"cannot reach {url}: {exc}", url=url)
        else:
            if 200 <= status < 300:
                return body.decode("utf-8", "replace")
            error = HttpError(f"HTTP {status} from {url}", url=url, status=status)
        if attempt == retries or not _retryable(error) or options.out_of_budget:
            raise error
        options.sleeper(_backoff(error, attempt))
    raise HttpError(f"cannot reach {url}", url=url)  # pragma: no cover


def _retryable(error: HttpError) -> bool:
    return error.status is None or error.status in RETRY_STATUSES


def _backoff(error: HttpError, attempt: int) -> float:
    """Wait as asked, up to a bound; otherwise back off exponentially."""
    if error.retry_after:
        try:
            requested = float(error.retry_after.strip())
        except ValueError:
            requested = 0.0
        if requested > 0:
            return min(requested, MAX_RETRY_AFTER)
    return BACKOFF * (2**attempt)


def _read_cache(cache_dir: Path, url: str) -> str | None:
    path = cache_path(cache_dir, url)
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("url") != url:
        return None
    body = payload.get("body")
    return body if isinstance(body, str) else None


def _write_cache(cache_dir: Path, url: str, body: str) -> None:
    path = cache_path(cache_dir, url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_text(json.dumps({"url": url, "body": body}), "utf-8")
        temporary.replace(path)
    except OSError:
        # A cache that cannot be written must never fail the lookup.
        return
