"""Text normalisation and the parsing helpers every layer shares.

Everything here is pure: strings in, strings (or ``None``) out. The rules are
deliberately conservative — a value that cannot be recognised is dropped rather
than guessed, because ``apr`` reports honestly uncertain metadata.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

#: New-style arXiv identifier: ``2306.12983`` (``v2`` optional).
ARXIV_NEW = re.compile(r"\b(?P<id>\d{4}\.\d{4,5})(?P<version>v\d+)?\b")
#: Old-style arXiv identifier: ``hep-th/9901001`` or ``math.GT/0309136``.
ARXIV_OLD = re.compile(
    r"\b(?P<id>[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?P<version>v\d+)?\b"
)
#: A DOI, optionally behind a ``doi:`` prefix or a resolver URL.
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
#: ``arXiv:2306.12983v2`` as printed in the left margin of arXiv PDFs.
ARXIV_STAMP = re.compile(
    r"arXiv:\s*(?P<id>\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})"
    r"(?P<version>v\d+)?"
)
#: ``https://arxiv.org/abs/2505.21469v4``.
ARXIV_URL = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?P<version>v\d+)?"
)
#: The DataCite DOI arXiv assigns: ``10.48550/arXiv.2505.21469``.
ARXIV_DOI = re.compile(
    r"10\.48550/arxiv\.(?P<id>\d{4}\.\d{4,5})(?P<version>v\d+)?", re.IGNORECASE
)
#: Springer Nature article numbers double as file names: ``s41467-024-53632-4``.
SPRINGER_ARTICLE = re.compile(r"^s(\d{5}-\d{3}-\d{5}-[a-z0-9])$", re.IGNORECASE)

#: A four-digit year. Lookarounds rather than ``\b`` because file names glue
#: years to their neighbours: ``ICLR_2024_paper.pdf``.
YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

#: A spacing acute accent (U+00B4) written before its letter, as in
#: ``Dubi`` + U+00B4 + ``nski``, or its NFKC expansion ``space + combining acute``.
_ACCENT_BEFORE = re.compile(r"[\u00b4\u02b9\u0060]\s*([^\W\d_])")
_ACCENT_AFTER_SPACE = re.compile(r"\s\u0301([^\W\d_])")
#: Glyphs that mark an author: the asterisk (U+002A and its U+2217 variant),
#: the dagger family and the section signs.
MARKER_GLYPHS = "\u002a\u2217\u2020\u2021\u00a7\u00b6"
#: Superscript markers attached to an author name: they may sit inside the name
#: as ``Name 1,2`` or directly after it.
_AUTHOR_MARKERS = re.compile(rf"[\s,;]*\d+(?:[,;]\d+)*[\s,;]*|[{MARKER_GLYPHS}]+")
#: Characters that separate one author from the next in an author list.
AUTHOR_SEPARATORS = re.compile(r";|\s+and\s+|&|,")

_AFFILIATION_WORDS = re.compile(
    r"(?i)\b(university|universit|institute|institut|department|laborator|"
    r"school|college|academy|research|faculty|center|centre|hospital|"
    r"corporation|inc\.|gmbh|email|abstract)\b"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
#: A word as it appears in a name: letters, optional inner punctuation.
_NAME_WORD = re.compile(r"^[^\W\d_][\w'\u2019.\-\u00b4]*$", re.UNICODE)


def nfc(text: str) -> str:
    """Normalise to NFC, which is what JSON consumers expect."""
    return unicodedata.normalize("NFC", text)


def repair_spacing_accents(text: str) -> str:
    """Turn a spacing acute accent into a combining one.

    ``Dubi`` + U+00B4 + ``nski`` becomes ``Dubi\u0144ski``: some TeX fonts place an
    accent glyph before the letter it belongs to, and NFKC turns that glyph into
    a space followed by a combining accent. Both spellings are repaired; a real
    standalone accent is left alone because a letter must follow it.
    """
    text = _ACCENT_AFTER_SPACE.sub(lambda match: match.group(1) + "\u0301", text)
    text = _ACCENT_BEFORE.sub(lambda match: match.group(1) + "\u0301", text)
    return nfc(text)


def clean_author(text: str) -> str:
    """Drop markers, emails and affiliation dust from one author string."""
    text = _EMAIL.sub(" ", text)
    text = _URL.sub(" ", text)
    text = repair_spacing_accents(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2009", " ").replace("\u00a0", " ")
    text = _AUTHOR_MARKERS.sub(" ", text)
    text = re.sub(r"\s*,\s*$", "", text)
    text = re.sub(r"^\s*[,;&]\s*", "", text)
    return " ".join(nfc(text).split())


def looks_like_name(text: str) -> bool:
    """True when a string could be a person's name and not an affiliation."""
    if not text or "@" in text or re.search(r"\d", text):
        return False
    if _AFFILIATION_WORDS.search(text) or _URL.search(text):
        return False
    words = text.split()
    if not 1 <= len(words) <= 5:
        return False
    return all(_NAME_WORD.match(word) for word in words)


def split_author_list(text: str) -> list[str]:
    """Split a metadata author field into names, validating each one."""
    return [name for name in author_parts(text) if looks_like_name(name)]


def author_parts(text: str) -> list[str]:
    """Split an author list on its separators, without validating the parts."""
    return [clean_author(part) for part in AUTHOR_SEPARATORS.split(text)]


def normalise_doi(text: str) -> str | None:
    """Return a lowercase DOI without the ``doi:`` prefix, or None."""
    match = DOI.search(text)
    if not match:
        return None
    doi = match.group(1)
    doi = doi.rstrip(".,;:)]}'\"")
    # Publisher punctuation is not part of the DOI: 10.1234/x).  -> 10.1234/x
    doi = doi.rstrip(".")
    return doi.lower() if len(doi) > 4 else None


def doi_from_filename(name: str) -> str | None:
    """Recognise a DOI in a file name, including the Springer article form."""
    stem = name.rsplit(".", 1)[0]
    article = SPRINGER_ARTICLE.match(stem)
    if article:
        return f"10.1038/s{article.group(1).lower()}"
    return normalise_doi(name)


def find_arxiv(text: str) -> tuple[str, str | None] | None:
    """Return ``(id, version)`` for the first arXiv identifier in ``text``."""
    for pattern in (ARXIV_STAMP, ARXIV_URL, ARXIV_DOI, ARXIV_NEW, ARXIV_OLD):
        match = pattern.search(text)
        if match:
            version = match.groupdict().get("version")
            return match.group("id"), version
    return None


def arxiv_year(identifier: str) -> int | None:
    """The year an arXiv identifier encodes: ``2610.01939`` was submitted in 2026.

    The identifier starts with the two-digit year and month; the 91 split is
    arXiv's own rule for identifiers minted last century.
    """
    match = re.match(r"(\d{2})(\d{2})\.\d{4,5}$", identifier)
    if not match:
        return None
    year = int(match.group(1))
    return (1900 if year >= 91 else 2000) + year


def normalise_title(text: str) -> str:
    """Whitespace-normalise a title and repair accent ordering."""
    return " ".join(repair_spacing_accents(nfc(text)).split())


def plausible_title(text: str) -> bool:
    """Reject the placeholder strings PDF producers sometimes put in ``/Title``."""
    folded = text.strip().lower()
    if len(folded) < 8 or not re.search(r"[^\W\d_]", folded):
        return False
    return folded not in {"untitled", "unknown", "document", "no title"}


def join_wrapped(lines: Sequence[str]) -> str:
    """Join title lines, repairing end-of-line hyphenation.

    ``['MOD-', 'ELS']`` becomes ``MODELS``: a hyphen at the end of a line is a
    break inside a word, not punctuation. Capitalisation is left untouched.
    """
    out = ""
    for line in lines:
        piece = line.strip()
        if not piece:
            continue
        if not out:
            out = piece
            continue
        if out.endswith("-") and not out.endswith("--"):
            out = out[:-1] + piece
        else:
            out = out + " " + piece
    return out.strip()


def strip_markers(text: str) -> str:
    """Remove superscript markers from a text run."""
    return " ".join(_AUTHOR_MARKERS.sub(" ", text).split())
