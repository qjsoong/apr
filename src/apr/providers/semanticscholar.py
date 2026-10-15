"""Semantic Scholar: a paper by DOI, or its best title match."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from apr.match import (
    Candidate,
    abstract_from_inverted_index,
    canonical_url,
    normalise_doi,
    parse_arxiv_id,
)
from apr.providers import http
from apr.providers.http import HttpOptions

API = "https://api.semanticscholar.org/graph/v1/paper"
#: The fields every call asks for; S2 returns nothing else.
FIELDS = "title,authors,year,venue,externalIds,abstract,publicationVenue"


def by_doi(doi: str, *, options: HttpOptions) -> Candidate | None:
    """The paper Semantic Scholar has under this DOI."""
    url = f"{API}/DOI:{quote(doi, safe='/')}?{urlencode({'fields': FIELDS})}"
    payload = _get_json(url, options=options)
    return _from_paper(payload)


def match(title: str, *, options: HttpOptions) -> list[Candidate]:
    """The single best title match, or nothing when S2 finds none.

    ``/paper/search/match`` answers 404 when it cannot match the query, which is
    the same answer as an empty candidate list to us.
    """
    url = f"{API}/search/match?{urlencode({'query': title, 'fields': FIELDS})}"
    payload = _get_json(url, options=options)
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    candidates = [_from_paper(paper) for paper in data]
    return [candidate for candidate in candidates if candidate is not None]


def _get_json(url: str, *, options: HttpOptions) -> Any:
    try:
        return http.get_json(url, options=options)
    except http.HttpError as exc:
        if exc.not_found:
            return None
        raise


def _from_paper(paper: Any) -> Candidate | None:
    if not isinstance(paper, dict):
        return None
    ids = paper.get("externalIds")
    ids = ids if isinstance(ids, dict) else {}
    doi = normalise_doi(ids.get("DOI"))
    arxiv = parse_arxiv_id(ids.get("ArXiv"))
    arxiv_id, version = arxiv or (None, None)
    title = paper.get("title")
    title = title if isinstance(title, str) and title.strip() else None
    if title is None and doi is None and arxiv_id is None:
        return None
    return Candidate(
        title=title,
        authors=[name for name in map(_author_name, _authors(paper)) if name],
        year=_year(paper.get("year")),
        venue=_venue(paper),
        doi=doi,
        arxiv_id=arxiv_id,
        arxiv_version=version,
        url=canonical_url(doi=doi, arxiv_id=arxiv_id, version=version),
        abstract=_abstract(paper.get("abstract")),
    )


def _authors(paper: dict[str, Any]) -> list[Any]:
    authors = paper.get("authors")
    return authors if isinstance(authors, list) else []


def _author_name(entry: Any) -> str | None:
    name = entry.get("name") if isinstance(entry, dict) else None
    return " ".join(name.split()) if isinstance(name, str) and name.strip() else None


def _venue(paper: dict[str, Any]) -> str | None:
    venue = paper.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        publication = paper.get("publicationVenue")
        venue = publication.get("name") if isinstance(publication, dict) else None
    return " ".join(venue.split()) if isinstance(venue, str) and venue.strip() else None


def _abstract(value: Any) -> str | None:
    """S2 usually returns plain text, but some records carry an inverted index."""
    if isinstance(value, str):
        return " ".join(value.split()) or None
    return abstract_from_inverted_index(value)


def _year(value: Any) -> int | None:
    return value if isinstance(value, int) and 1000 <= value <= 2999 else None
