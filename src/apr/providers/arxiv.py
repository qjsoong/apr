"""arXiv's Atom API: the paper by id, or a search by title and author."""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from urllib.parse import urlencode

from apr.match import (
    Candidate,
    canonical_url,
    normalise_doi,
    parse_arxiv_id,
    surname,
)
from apr.providers import http
from apr.providers.http import HttpOptions

API = "https://export.arxiv.org/api/query"
MAX_RESULTS = 5

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def by_id(arxiv_id: str, *, options: HttpOptions) -> Candidate | None:
    """The paper with this arXiv id, as the API knows it."""
    query = urlencode({"id_list": arxiv_id, "max_results": "1"})
    return _first(http.get_text(f"{API}?{query}", options=options))


def search(
    title: str, authors: Sequence[str] = (), *, options: HttpOptions
) -> list[Candidate]:
    """Papers whose title matches, narrowed by the first author when we have one."""
    terms = [f'ti:"{_term(title)}"']
    first_author = surname(authors[0]) if authors else ""
    if first_author:
        terms.append(f'au:"{_term(first_author)}"')
    query = urlencode(
        {"search_query": " AND ".join(terms), "max_results": str(MAX_RESULTS)}
    )
    return _candidates(http.get_text(f"{API}?{query}", options=options))


def _term(text: str) -> str:
    """A search term that cannot close the quoted phrase it sits in."""
    return " ".join(text.replace('"', " ").split())


def _candidates(text: str) -> list[Candidate]:
    root = ElementTree.fromstring(text)
    return [
        candidate
        for candidate in (_candidate(entry) for entry in root.findall(f"{ATOM}entry"))
        if candidate is not None
    ]


def _first(text: str) -> Candidate | None:
    candidates = _candidates(text)
    return candidates[0] if candidates else None


def _candidate(entry: ElementTree.Element) -> Candidate | None:
    arxiv = parse_arxiv_id(_text(entry.find(f"{ATOM}id")))  # None for error entries
    if arxiv is None:
        return None
    arxiv_id, version = arxiv
    doi = normalise_doi(_text(entry.find(f"{ARXIV}doi")))
    names = (node.text for node in entry.findall(f"{ATOM}author/{ATOM}name"))
    return Candidate(
        title=_text(entry.find(f"{ATOM}title")),
        authors=[name for name in (_collapse(node) for node in names) if name],
        year=_year(_text(entry.find(f"{ATOM}published"))),
        venue=_text(entry.find(f"{ARXIV}journal_ref")) or "arXiv",
        doi=doi,
        arxiv_id=arxiv_id,
        arxiv_version=version,
        url=canonical_url(doi=doi, arxiv_id=arxiv_id, version=version),
        abstract=_text(entry.find(f"{ATOM}summary")),
    )


def _text(node: ElementTree.Element | None) -> str | None:
    return _collapse(node.text) if node is not None else None


def _collapse(text: str | None) -> str | None:
    return " ".join(text.split()) or None if text else None


def _year(published: str | None) -> int | None:
    """Year of the first version, from the ISO timestamp arXiv publishes."""
    if not published or len(published) < 4 or not published[:4].isdigit():
        return None
    return int(published[:4])
