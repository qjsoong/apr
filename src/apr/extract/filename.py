"""Hints from the file name: the weakest layer, but often the only one.

A file name is evidence about how the file was saved, not about the paper, so
everything here is offered at low confidence and nothing is ever treated as
authoritative.
"""

from __future__ import annotations

import re
from pathlib import Path

from apr.extract.layers import SOURCE_FILENAME, LayerResult
from apr.extract.normalize import (
    ARXIV_NEW,
    ARXIV_OLD,
    YEAR,
    arxiv_year,
    doi_from_filename,
    find_arxiv,
    normalise_title,
)

#: Conference and journal abbreviations that appear in file names.
VENUE_ACRONYMS = (
    "iclr",
    "nips",
    "neurips",
    "icml",
    "cvpr",
    "wacv",
    "eccv",
    "iccv",
    "acl",
    "emnlp",
    "naacl",
    "aaai",
    "kdd",
    "sigir",
    "tmlr",
    "jmlr",
)

FILE_NAME_VENUE = re.compile(
    r"\b(" + "|".join(VENUE_ACRONYMS) + r")\b[\s_-]*(?P<year>19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)

#: Words that describe the file rather than the paper.
_FILE_NOISE = {
    "paper",
    "preprint",
    "camera",
    "ready",
    "conference",
    "proceedings",
    "final",
    "accepted",
    "submitted",
    "supplementary",
    "appendix",
}

_SEPARATORS = re.compile(r"[\s_\-()\[\]+,.]+")


def filename_layer(path: str | Path) -> LayerResult:
    """Offer what the file name alone suggests."""
    layer = LayerResult(source=SOURCE_FILENAME)
    name = Path(path).name
    stem = Path(path).stem

    arxiv = find_arxiv(stem)
    if arxiv:
        layer.offer("arxiv_id", arxiv[0], method="name:arxiv-id", confidence=0.5)
        if arxiv[1]:
            layer.offer(
                "arxiv_version", arxiv[1], method="name:arxiv-id", confidence=0.5
            )
        number_year = arxiv_year(arxiv[0])
        if number_year:
            layer.offer(
                "year", number_year, method="name:arxiv-id-year", confidence=0.4
            )

    doi = doi_from_filename(name)
    if doi:
        layer.offer("doi", doi, method="name:doi", confidence=0.5)

    without_ids = ARXIV_NEW.sub(" ", ARXIV_OLD.sub(" ", stem))
    without_ids = without_ids.replace("-", " ")
    year = YEAR.search(without_ids)
    if year:
        layer.offer("year", int(year.group(1)), method="name:year", confidence=0.4)

    venue = FILE_NAME_VENUE.search(stem)
    if venue:
        layer.offer(
            "venue",
            f"{venue.group(1).upper()} {venue.group(2)}",
            method="name:venue",
            confidence=0.35,
        )

    title = _title_slug(stem)
    if title:
        layer.offer("title", title, method="name:words", confidence=0.3)
    return layer


def _title_slug(stem: str) -> str | None:
    """A lowercase title guess from a descriptive file name, or None.

    Only descriptive names survive: identifiers, years and file-management
    words are removed, and at least four words must be left.
    """
    without_ids = ARXIV_NEW.sub(" ", ARXIV_OLD.sub(" ", stem))
    words = [word for word in _SEPARATORS.split(without_ids) if word]
    kept = [
        word
        for word in words
        if not YEAR.fullmatch(word)
        and not word.isdigit()
        and word.lower() not in _FILE_NOISE
        and word.lower() not in VENUE_ACRONYMS
    ]
    if len(kept) < 4:
        return None
    if not all(word.isalpha() for word in kept):
        return None
    return normalise_title(" ".join(kept))
