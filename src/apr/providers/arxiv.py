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


def by_id(
    arxiv_id: str, *, reported_version: str | None = None, options: HttpOptions
) -> Candidate | None:
    """The paper with this arXiv id, as the API knows it.

    ``reported_version`` is the version the file claims (see
    :func:`apr.match.parse_arxiv_id`); it decides which date the year comes
    from, because the API always answers with the latest version.
    """
    query = urlencode({"id_list": arxiv_id, "max_results": "1"})
    return _first(http.get_text(f"{API}?{query}", options=options), reported_version)


def search(
    title: str,
    authors: Sequence[str] = (),
    *,
    reported_version: str | None = None,
    options: HttpOptions,
) -> list[Candidate]:
    """Papers whose title matches, narrowed by the first author when we have one."""
    terms = [f'ti:"{_term(title)}"']
    first_author = surname(authors[0]) if authors else ""
    if first_author:
        terms.append(f'au:"{_term(first_author)}"')
    query = urlencode(
        {"search_query": " AND ".join(terms), "max_results": str(MAX_RESULTS)}
    )
    return _candidates(
        http.get_text(f"{API}?{query}", options=options), reported_version
    )


def _term(text: str) -> str:
    """A search term that cannot close the quoted phrase it sits in."""
    return " ".join(text.replace('"', " ").split())


def _candidates(text: str, reported_version: str | None = None) -> list[Candidate]:
    root = ElementTree.fromstring(text)
    return [
        candidate
        for candidate in (
            _candidate(entry, reported_version)
            for entry in root.findall(f"{ATOM}entry")
        )
        if candidate is not None
    ]


def _first(text: str, reported_version: str | None = None) -> Candidate | None:
    candidates = _candidates(text, reported_version)
    return candidates[0] if candidates else None


def _candidate(
    entry: ElementTree.Element, reported_version: str | None = None
) -> Candidate | None:
    arxiv = parse_arxiv_id(_text(entry.find(f"{ATOM}id")))  # None for error entries
    if arxiv is None:
        return None
    arxiv_id, version = arxiv
    doi = normalise_doi(_text(entry.find(f"{ARXIV}doi")))
    journal_ref = _text(entry.find(f"{ARXIV}journal_ref"))
    year, note = _year_and_note(entry, version, reported_version)
    names = (node.text for node in entry.findall(f"{ATOM}author/{ATOM}name"))
    return Candidate(
        title=_text(entry.find(f"{ATOM}title")),
        authors=[name for name in (_collapse(node) for node in names) if name],
        year=year,
        venue=journal_ref or "arXiv",
        doi=doi,
        arxiv_id=arxiv_id,
        arxiv_version=version,
        url=canonical_url(doi=doi, arxiv_id=arxiv_id, version=version),
        abstract=_text(entry.find(f"{ATOM}summary")),
        # A journal reference means the record describes a published paper.
        type="article" if journal_ref else "preprint",
        note=note,
    )


def _text(node: ElementTree.Element | None) -> str | None:
    return _collapse(node.text) if node is not None else None


def _collapse(text: str | None) -> str | None:
    return " ".join(text.split()) or None if text else None


def _year_and_note(
    entry: ElementTree.Element, version: str | None, reported_version: str | None
) -> tuple[int | None, str | None]:
    """The year of the version in the file, and a note when it is not the latest.

    ``<published>`` dates the first version and ``<updated>`` the latest one, and
    the API always answers with the latest. When the file claims that version,
    the updated year is the one the file holds; when the file holds an older
    version, the first version's year is the honest one, with a note saying that
    the file is not what arXiv now serves.
    """
    published = _year(_text(entry.find(f"{ATOM}published")))
    if not reported_version or not version:
        return published, None
    if reported_version == version:
        return _year(_text(entry.find(f"{ATOM}updated"))) or published, None
    return published, (
        f"the file is version {reported_version} but arXiv reports {version} "
        f"as the latest; the year is the first version's"
    )


def _year(timestamp: str | None) -> int | None:
    """Year of an ISO timestamp from the feed."""
    if not timestamp or len(timestamp) < 4 or not timestamp[:4].isdigit():
        return None
    return int(timestamp[:4])
