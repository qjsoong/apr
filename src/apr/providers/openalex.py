"""OpenAlex: works by DOI and by free-text search, with inverted abstracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote, urlencode

from apr.match import (
    Candidate,
    abstract_from_inverted_index,
    canonical_url,
    invert_name,
    normalise_doi,
    parse_arxiv_id,
)
from apr.providers import http
from apr.providers.http import HttpOptions

API = "https://api.openalex.org/works"
SEARCH_ROWS = 5


def by_doi(
    doi: str, *, mailto: str | None = None, options: HttpOptions
) -> Candidate | None:
    """The work OpenAlex has under this DOI."""
    url = f"{API}/doi:{quote(doi, safe='/')}"
    payload = _get_json(_polite(url, mailto), options=options)
    return _from_work(payload)


def search(
    title: str,
    authors: Sequence[str] = (),
    *,
    mailto: str | None = None,
    options: HttpOptions,
) -> list[Candidate]:
    """Candidate works for a title search."""
    params = {"search": title, "per-page": str(SEARCH_ROWS)}
    if mailto:
        params["mailto"] = mailto
    payload = _get_json(f"{API}?{urlencode(params)}", options=options)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    candidates = [_from_work(work) for work in results]
    return [candidate for candidate in candidates if candidate is not None]


def _polite(url: str, mailto: str | None) -> str:
    return f"{url}?{urlencode({'mailto': mailto})}" if mailto else url


def _get_json(url: str, *, options: HttpOptions) -> Any:
    try:
        return http.get_json(url, options=options)
    except http.HttpError as exc:
        if exc.not_found:
            return None
        raise


def _from_work(work: Any) -> Candidate | None:
    if not isinstance(work, dict):
        return None
    title = work.get("title") or work.get("display_name")
    title = title if isinstance(title, str) and title.strip() else None
    doi = normalise_doi(work.get("doi"))
    arxiv = parse_arxiv_id(_landing_page(work))
    arxiv_id, version = arxiv or (None, None)
    if title is None and doi is None and arxiv_id is None:
        return None
    return Candidate(
        title=title,
        authors=[
            name for name in map(_display_name, _list(work, "authorships")) if name
        ],
        year=_year(work.get("publication_year")),
        venue=_venue(work),
        doi=doi,
        arxiv_id=arxiv_id,
        arxiv_version=version,
        url=canonical_url(doi=doi, arxiv_id=arxiv_id, version=version),
        abstract=abstract_from_inverted_index(work.get("abstract_inverted_index")),
        type=_type(work),
    )


def _display_name(authorship: Any) -> str | None:
    if not isinstance(authorship, dict):
        return None
    author = authorship.get("author")
    name = author.get("display_name") if isinstance(author, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None
    # OpenAlex mixes "Cheng Zeng" and "Zeng, Cheng" inside one author list.
    return invert_name(name)


def _type(work: dict[str, Any]) -> str | None:
    """OpenAlex's record type, e.g. ``article`` or ``preprint``."""
    value = work.get("type")
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _landing_page(work: dict[str, Any]) -> str:
    location = work.get("primary_location")
    landing = location.get("landing_page_url") if isinstance(location, dict) else None
    return landing if isinstance(landing, str) else ""


def _venue(work: dict[str, Any]) -> str | None:
    location = work.get("primary_location")
    source = location.get("source") if isinstance(location, dict) else None
    name = source.get("display_name") if isinstance(source, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None
    text = " ".join(name.split())
    # OpenAlex names the preprint server "arXiv (Cornell University)".
    return "arXiv" if text.lower().startswith("arxiv") else text


def _year(value: Any) -> int | None:
    return value if isinstance(value, int) and 1000 <= value <= 2999 else None


def _list(work: dict[str, Any], key: str) -> list[Any]:
    value = work.get(key)
    return value if isinstance(value, list) else []
