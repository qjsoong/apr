"""Normalisation and scoring for online resolution. Pure: no I/O.

Two jobs, both free of state so they can be tested on their own:

* turn what a service returned into the values the record expects (DOIs, arXiv
  ids, author names, canonical URLs, abstracts);
* measure how well a candidate agrees with what the PDF already told us, so
  that a wrong search hit loses to a right one instead of polluting the record.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

#: Weights of the score components. Components neither side can supply are left
#: out and the rest renormalised, so a missing year never counts as a mismatch.
TITLE_WEIGHT = 0.80
AUTHOR_WEIGHT = 0.15
YEAR_WEIGHT = 0.05

#: A bibliographic candidate below this is not offered at all.
MATCH_THRESHOLD = 0.60

#: Below this a match is still offered, but with a warning to check it.
CONFIDENT_MATCH = 0.85

#: Below this a looked-up record is reported as disagreeing with the PDF text.
CROSS_CHECK_THRESHOLD = 0.5

#: How many of the local authors are compared; papers list many, and the first
#: few carry the signal.
AUTHORS_COMPARED = 3


@dataclass(slots=True)
class Candidate:
    """One provider's parsed answer, before it is offered to a record."""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    arxiv_version: str | None = None
    url: str | None = None
    abstract: str | None = None


# --------------------------------------------------------------------------
# identifiers


_DOI_PREFIX = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*|urn:doi:|info:doi/)", re.IGNORECASE
)
_DOI_TRAILING = ".,;:)]}>\"\u201d'"
_DOI_BODY = re.compile(r"^10\.\d{4,9}/\S+$")

_ARXIV_PREFIX = re.compile(
    r"^(?:arxiv:\s*|https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)", re.IGNORECASE
)
_ARXIV_BODY = (
    re.compile(r"^(?P<id>\d{4}\.\d{4,5})(?P<version>v\d+)?$"),
    re.compile(r"^(?P<id>[a-z-]+(?:\.[A-Za-z-]+)?/\d{7})(?P<version>v\d+)?$"),
)


def normalise_doi(value: Any) -> str | None:
    """Lowercase a DOI, dropping the URL form and trailing punctuation.

    Returns None for anything that does not look like a DOI, so a stray item
    from an API can never be stored as one.
    """
    if not isinstance(value, str):
        return None
    text = _DOI_PREFIX.sub("", value.strip())
    text = text.rstrip(_DOI_TRAILING).strip().lower()
    return text if _DOI_BODY.match(text) else None


def parse_arxiv_id(value: Any) -> tuple[str, str | None] | None:
    """Return ``(id, version)`` for anything that names an arXiv paper."""
    if not isinstance(value, str):
        return None
    text = _ARXIV_PREFIX.sub("", value.strip())
    text = text.removesuffix(".pdf").rstrip("/").strip()
    for pattern in _ARXIV_BODY:
        found = pattern.match(text)
        if found:
            return found.group("id"), found.group("version")
    return None


def arxiv_url(arxiv_id: str, version: str | None = None) -> str:
    """abs page of one version, e.g. ``https://arxiv.org/abs/2610.01939v2``."""
    return f"https://arxiv.org/abs/{arxiv_id}{version or ''}"


def canonical_url(
    *, doi: str | None = None, arxiv_id: str | None = None, version: str | None = None
) -> str | None:
    """Landing page for the canonical record of a set of identifiers.

    arXiv wins when the id is known: the abs page is free, stable and pins the
    version. The DOI is reported in its own field either way.
    """
    if arxiv_id:
        return arxiv_url(arxiv_id, version)
    if doi:
        return f"https://doi.org/{doi}"
    return None


# --------------------------------------------------------------------------
# names and titles


_MARKUP_BLOCK = re.compile(
    r"</?(?:jats:)?(?:p|sec|abstract|title|br|div|li|tr|table|pre)\b[^>]*>",
    re.IGNORECASE,
)
_MARKUP_ANY = re.compile(r"<[^>]+>")


def strip_markup(text: Any) -> str:
    """Remove JATS/HTML tags and unescape entities from an abstract or title.

    Block-level tags become a space so that paragraphs do not run together;
    inline tags such as ``<jats:italic>`` disappear without breaking the word.
    """
    if not isinstance(text, str):
        return ""
    text = _MARKUP_BLOCK.sub(" ", text)
    text = _MARKUP_ANY.sub("", text)
    return " ".join(html.unescape(text).split())


def full_name(given: Any, family: Any) -> str | None:
    """Join author name parts as printed: ``'Kehan'`` + ``'Wu'``."""
    parts = [
        " ".join(str(part).split())
        for part in (given, family)
        if isinstance(part, str) and part.strip()
    ]
    return " ".join(parts) or None


def invert_name(name: Any) -> str:
    """``'Dubiński, Jan'`` -> ``'Jan Dubiński'``; other forms are kept."""
    if not isinstance(name, str):
        return ""
    text = " ".join(name.split())
    if "," not in text:
        return text
    family, _, given = text.partition(",")
    return " ".join(part for part in (given.strip(), family.strip()) if part)


_NON_NAME = re.compile(r"[^\w\s'-]", re.UNICODE)
_HYPHEN_BREAK = re.compile(r"(?<=[a-z])-\s+(?=[a-z])")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def _fold(text: str) -> str:
    """Lowercase, drop diacritics, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().split())


def normalise_title(text: Any) -> str:
    """Fold a title for comparison.

    Case, accents and punctuation disappear, so only real differences remain.
    A hyphen that the layout used to break a word (``genera-\\n tion``) is
    joined rather than split, and a leading article is dropped.
    """
    if not isinstance(text, str):
        return ""
    text = _fold(text.replace("\u00ad", "").replace("\u2010", "-"))
    text = _NON_WORD.sub(" ", _HYPHEN_BREAK.sub("", text))
    return " ".join(text.split()).removeprefix("the ")


def _author_key(name: Any) -> tuple[str, str]:
    """Surname and first initial of a name, whatever order it is printed in."""
    if isinstance(name, str) and "," in name:
        name = invert_name(name)
    parts = _NON_NAME.sub(" ", _fold(name or "")).split()
    if not parts:
        return ("", "")
    return (parts[-1], parts[0][:1])


def surname(name: Any) -> str:
    """Last name of a printed author, as printed, for a search query.

    Deliberately *not* folded: Crossref's author query is diacritic-sensitive,
    so ``Dubiński`` has to travel with its accent to find the paper.
    """
    if not isinstance(name, str) or not name.strip():
        return ""
    return invert_name(name).rsplit(" ", 1)[-1]


def author_score(local: Sequence[str], candidate: Sequence[str]) -> float:
    """Fraction of the first local authors that the candidate also lists.

    A match is the same surname plus the same first initial, which survives
    ``Dubiński``/``Dubinski``, ``J. Dubiński``/``Jan Dubiński`` and name order.
    """
    checked = [name for name in local if name][:AUTHORS_COMPARED]
    if not checked:
        return 0.0
    keys = {_author_key(name) for name in candidate if name}
    keys.discard(("", ""))
    if not keys:
        return 0.0
    return sum(1 for name in checked if _author_key(name) in keys) / len(checked)


def year_score(local: int | None, candidate: int | None) -> float:
    """1.0 for the same year, less for a near miss, 0 for a different decade."""
    if local is None or candidate is None:
        return 0.0
    return max(0.0, 1.0 - abs(int(local) - int(candidate)) / 3.0)


def title_score(left: Any, right: Any) -> float:
    """Blend token-set overlap with difflib's edit similarity.

    Token overlap survives word order and punctuation; the edit ratio keeps
    two titles that share only stopwords apart. Neither alone is enough for the
    titles scholarly APIs return.
    """
    first, second = normalise_title(left), normalise_title(right)
    if not first or not second:
        return 0.0
    tokens_left, tokens_right = set(first.split()), set(second.split())
    overlap = len(tokens_left & tokens_right) / len(tokens_left | tokens_right)
    ratio = SequenceMatcher(None, first, second, autojunk=False).ratio()
    return 0.5 * overlap + 0.5 * ratio


def score_candidate(
    candidate: Candidate,
    *,
    title: str | None,
    authors: Sequence[str] = (),
    year: int | None = None,
) -> float:
    """How well a candidate agrees with what the file already told us.

    Only the components both sides can supply count, and they are renormalised:
    a candidate is not punished for a field the PDF never had.
    """
    components: list[tuple[float, float]] = [
        (TITLE_WEIGHT, title_score(title, candidate.title))
    ]
    if authors and candidate.authors:
        components.append((AUTHOR_WEIGHT, author_score(authors, candidate.authors)))
    if year is not None and candidate.year is not None:
        components.append((YEAR_WEIGHT, year_score(year, candidate.year)))
    total = sum(weight for weight, _ in components)
    return sum(weight * value for weight, value in components) / total


def cross_check(
    candidate: Candidate, *, title: str | None, authors: Sequence[str] = ()
) -> float | None:
    """How well an identifier-resolved record agrees with the PDF text.

    A DOI or arXiv id found in a file can be stale, mistyped, or another
    paper's; the record it resolves to should still look like the paper the
    file claims to be. Returns None when there is nothing to compare, because
    the PDF gave no title. Otherwise it is the title similarity, capped by the
    author agreement when both sides list authors, so a matching title with a
    different author list is reported too.

    The value reports, it does not decide: an identifier-resolved record is
    still authoritative for that identifier.
    """
    if not (title and title.strip()) or not (
        candidate.title and candidate.title.strip()
    ):
        return None
    agreement = title_score(title, candidate.title)
    if authors and candidate.authors:
        agreement = min(agreement, author_score(authors, candidate.authors))
    return agreement


def abstract_from_inverted_index(index: Any) -> str | None:
    """Rebuild a plain-text abstract from OpenAlex's ``word -> positions`` map."""
    if not isinstance(index, dict) or not index:
        return None
    positions: dict[int, str] = {}
    for word, where in index.items():
        if not isinstance(word, str) or not isinstance(where, list):
            continue
        for position in where:
            if isinstance(position, int):
                positions[position] = word
    if not positions:
        return None
    return " ".join(positions[position] for position in sorted(positions))
