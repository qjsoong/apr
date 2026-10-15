"""The first page: the title, the author block and the abstract as printed.

Two views of the same page are offered. The layout view groups text spans by
baseline and font size, so it can rebuild a title that a producer truncated in
``/Title`` and can tell superscript markers from author names. The plain-text
view does not depend on geometry and is what remains when a PDF has no usable
positions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from apr.extract.document import PdfDocument, TextLine, layout_lines
from apr.extract.layers import SOURCE_PAGE, LayerResult
from apr.extract.normalize import (
    MARKER_GLYPHS,
    arxiv_year,
    author_parts,
    find_arxiv,
    join_wrapped,
    looks_like_name,
    normalise_doi,
    normalise_title,
    plausible_title,
)

#: pypdf inserts a space when the gap between two text runs looks wide. In a
#: small-caps title (two sizes per line, no lowercase) that guess misfires and
#: splits a word, e.g. ``MOD-`` + ``ELS`` or ``I`` + ``MPROVED``.
_SMALL_CAPS_RATIO = 0.95

#: A span that holds only superscript markers and separators.
_MARKER_SPAN = re.compile(rf"^[\s\d{MARKER_GLYPHS},;.]+$")
#: ``Name`` followed by a superscript marker: the shape of an author line.
#: A hyphenated compound (``GPT-6``) is not a name, so the hyphenated part
#: must be letters.
_MARKED_NAME = re.compile(
    rf"[A-Z][A-Za-z'\u2019.\u00b4]+(?:-[A-Za-z'\u2019.\u00b4]+)*\s?[\d{MARKER_GLYPHS}]"
)
#: Kickers: a journal's own label for the article type, never part of a title.
_KICKERS = {
    "article",
    "brief communication",
    "letter",
    "original article",
    "review article",
    "research article",
    "open access",
    "check for updates",
    "abstract",
    "comment",
    "editorial",
    "perspective",
}
_FURNITURE_PREFIX = re.compile(
    r"(?i)^\s*(?:"
    r"arxiv:\S+|"
    r"preprint\b|posted\b|draft\b|this is a pdf of\b|"
    r"published as\b|accepted (?:at|to|for)\b|to appear\b|under review\b|"
    r"received\b|accepted\b|published\b|revised\b|"
    r"check for updates\b|"
    r"doi:\S+|https?://\S+|www\.\S+"
    r")"
)
_JOURNAL_NAME = re.compile(r"(?i)^(?:nature|science|cell|pnas)\b[^|]{0,40}$")
_AFFILIATION = re.compile(
    r"(?i)\b(university|universit|institute|institut|laborator|department|"
    r"school|college|academy|faculty|research center|research centre|"
    r"epfl|eth zurich|nvidia|google|microsoft|meta ai)\b"
)
_ABSTRACT_HEADING = re.compile(r"(?i)^\s*(?:abstract|summary)\s*:?\s*$")
_INTRODUCTION = re.compile(r"(?i)^\s*(?:\d+|[ivx]+)?\s*introduction\s*:?\s*$")
_EMAIL_AT = "@"

CONFERENCE_HEADER = re.compile(
    r"(?i)published\s+as\s+a\s+conference\s+paper\s+at\s+"
    r"(?P<venue>[A-Za-z&.\-]+(?:\s+[A-Za-z&.\-]+)*?)\s*"
    r"(?P<year>19\d{2}|20\d{2})\b"
)
JOURNAL_VOLUME = re.compile(
    r"^\s*(?P<venue>[A-Z][A-Za-z&.\- ]{3,40}?)\s*\|\s*Volume\s+\d+\s*\|\s*"
    r"[A-Z][a-z]+\s+(?P<year>19\d{2}|20\d{2})\b"
)
NATURE_FOOTER = re.compile(
    r"^\s*(?P<venue>Nature[A-Za-z ]+?)\s*\|\s*\((?P<year>19\d{2}|20\d{2})\)"
)
CONFERENCE_FOOTER = re.compile(
    r"\((?P<venue>[A-Z]{2,10})\s+(?P<year>19\d{2}|20\d{2})\)"
)
PUBLISHED_DATE = re.compile(
    r"(?i)\b(?:published|accepted|received|online)\b[^\n]{0,40}?"
    r"(?P<year>19\d{2}|20\d{2})\b"
)
STAMP_DATE = re.compile(
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+"
    r"(?P<year>19\d{2}|20\d{2})\b"
)


@dataclass(frozen=True, slots=True)
class TitleRun:
    """The title as found in one view of the page."""

    text: str | None
    confidence: float
    end_index: int


def page_layer(
    doc: PdfDocument,
    *,
    metadata_title: str | None,
    metadata_authors: Sequence[str],
) -> LayerResult:
    """Offer everything the first page with text shows about the paper."""
    layer = LayerResult(source=SOURCE_PAGE)
    index, text, spans = doc.text_page()
    if index < 0:
        layer.warnings.append("no extractable text (scanned PDF?)")
        return layer
    if index > 0:
        layer.warnings.append(f"page 1 has no text layer; used page {index + 1}")

    lines = layout_lines(spans)
    text_lines = text.splitlines()

    layout_run = _layout_title(lines, doc.page_height(index))
    text_run = _text_title(text_lines)
    truncated = _metadata_title_truncated(metadata_title, layout_run.text) or (
        _metadata_title_truncated(metadata_title, text_run.text)
    )
    if truncated:
        layer.warnings.append(
            "embedded title looks truncated; used the title printed on page 1"
        )
    _offer_title(layer, layout_run, text_run, truncated=truncated)

    layout_authors = _layout_authors(lines, layout_run.end_index)
    text_authors = _text_authors(text_lines, text_run.end_index)
    authors_truncated = _authors_truncated(metadata_authors, layout_authors)
    if authors_truncated:
        layer.warnings.append(
            "embedded author list looks truncated; used the authors on page 1"
        )
    if layout_authors:
        layer.offer(
            "authors",
            layout_authors,
            method="layout:author-block",
            confidence=0.85 if authors_truncated else 0.7,
        )
    if text_authors:
        layer.offer(
            "authors",
            text_authors,
            method="text:author-block",
            confidence=0.85 if authors_truncated else 0.6,
        )

    _offer_abstract(layer, text_lines)
    _offer_identifiers(layer, text)
    _offer_publication(layer, text_lines, text)
    return layer


# -- title ------------------------------------------------------------------


def _line_text(line: TextLine, *, repair_caps: bool = True) -> str:
    """The text of one layout line, with the small-caps artefact repaired."""
    raw = line.raw
    if not repair_caps:
        return raw
    # A line with any lowercase glyph is not a small-caps line.
    sizes = {round(span.size, 2) for span in line.spans}
    if len(sizes) < 2 or raw != raw.upper():
        return raw
    smallest, largest = min(sizes), max(sizes)
    if smallest >= _SMALL_CAPS_RATIO * largest:
        return raw

    text = ""
    previous_size: float | None = None
    for span in line.spans:
        piece = span.text
        if (
            piece.startswith(" ")
            and previous_size is not None
            and span.size < previous_size
        ):
            # A word in a small-caps title starts with a glyph from the large
            # size; a leading space on a smaller span is pypdf's guess, not a
            # word boundary.
            piece = piece.lstrip(" ")
        text += piece
        previous_size = span.size
    return text.rstrip("\n")


def _layout_title(lines: Sequence[TextLine], page_height: float) -> TitleRun:
    """The title as a run of the largest text near the top of the page.

    Font size alone is not enough: arXiv margin stamps, figure labels and the
    publisher's own logotype are set larger than the title. The title is the
    largest text in the upper half of the page.
    """
    if not lines:
        return TitleRun(None, 0.0, 0)
    text_lines = [_line_text(line) for line in lines]
    top_limit = 0.5 * page_height
    top = [index for index, line in enumerate(lines) if line.y >= top_limit]
    in_top = top or list(range(len(lines)))
    largest = max(lines[index].size for index in in_top)
    if largest <= 0:
        return TitleRun(None, 0.0, 0)

    start = None
    for index in in_top:
        if lines[index].size < 0.85 * largest:
            continue
        if _is_furniture(text_lines[index]):
            continue
        start = index
        break
    if start is None:
        return TitleRun(None, 0.0, 0)

    run = [text_lines[start]]
    previous_y = lines[start].y
    end = start + 1
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.size < 0.6 * largest:
            break
        if previous_y - line.y > 1.6 * largest:
            break
        text = text_lines[index]
        if _is_furniture(text) or _stops_title(text):
            break
        run.append(text)
        previous_y = line.y
        end = index + 1
    joined = normalise_title(join_wrapped(run))
    if not plausible_title(joined):
        return TitleRun(None, 0.0, start + 1)
    return TitleRun(joined, _title_confidence(joined, from_layout=True), end)


def _text_title(text_lines: Sequence[str]) -> TitleRun:
    """The title as the first lines of the page's text, without geometry."""
    run: list[str] = []
    end = 0
    for index, raw in enumerate(text_lines):
        line = raw.strip()
        if not line:
            if run:
                break
            continue
        if not run:
            if _is_furniture(line) or _stops_title(line):
                continue
            if len(line) < 8 or not re.search(r"[^\W\d_]", line):
                continue
            run.append(line)
            end = index + 1
            continue
        if _stops_title(line) or _is_furniture(line):
            break
        if len(run) >= 4:
            break
        run.append(line)
        end = index + 1
    joined = normalise_title(join_wrapped(run))
    if not plausible_title(joined):
        return TitleRun(None, 0.0, end)
    return TitleRun(joined, _title_confidence(joined, from_layout=False), end)


def _title_confidence(text: str, *, from_layout: bool) -> float:
    """How much a title from the printed page can be trusted.

    A page title is normally the best evidence there is. Capitalisation and
    word spacing are the two ways it degrades: conference templates set titles
    in caps, and some TeX fonts drop the spaces between words.
    """
    score = 0.80 if from_layout else 0.75
    if re.search(r"[a-z]", text):
        score += 0.05
    else:
        score -= 0.08
    longest = max((len(word) for word in text.split()), default=0)
    if longest >= 18:
        score -= 0.10
    if not from_layout and re.search(r"\b[A-Z] [A-Z]{2,}", text):
        score -= 0.07
    if len(text.split()) < 3:
        score -= 0.05
    return round(max(0.5, min(0.9, score)), 2)


def _offer_title(
    layer: LayerResult,
    layout_run: TitleRun,
    text_run: TitleRun,
    *,
    truncated: bool,
) -> None:
    for run, method in (
        (layout_run, "layout:largest-font"),
        (text_run, "text:first-lines"),
    ):
        if not run.text:
            continue
        confidence = run.confidence
        if truncated:
            confidence = max(confidence, 0.85)
        layer.offer(
            "title",
            run.text,
            method=method,
            confidence=confidence,
            detail="metadata title truncated" if truncated else None,
        )


def _metadata_title_truncated(
    metadata_title: str | None, page_title: str | None
) -> bool:
    """True when the page prints more of the title than ``/Title`` holds.

    Producers cut a long title at a fixed length, so the embedded value is a
    prefix of the printed one and the cut lands inside a word or number.
    """
    if not metadata_title or not page_title:
        return False
    meta = _fold(metadata_title)
    page = _fold(page_title)
    if len(meta) < 12 or len(page) <= len(meta):
        return False
    if not page.startswith(meta):
        return False
    return page[len(meta)].isalnum()


def _fold(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


# -- authors ----------------------------------------------------------------


def _layout_authors(lines: Sequence[TextLine], start_index: int) -> list[str]:
    """Parse the author block using superscript spans as separators."""
    block: list[str] = []
    for line in lines[start_index:]:
        text = _line_text(line, repair_caps=False).strip()
        if not text:
            break
        if _stops_authors(text) or _AFFILIATION.search(text):
            break
        block.append(text)
        if len(block) >= 4:
            break
    if not block:
        return []

    names: list[str] = []
    for line in lines[start_index : start_index + len(block)]:
        names.extend(_names_from_line(line))
    return names


def _names_from_line(line: TextLine) -> list[str]:
    parts: list[str] = []
    current = ""
    for span in line.spans:
        if _MARKER_SPAN.match(span.text):
            if current.strip():
                parts.append(current)
            current = ""
            continue
        current += span.text
    if current.strip():
        parts.append(current)

    names: list[str] = []
    for part in parts:
        for candidate in author_parts(part):
            if looks_like_name(candidate):
                names.append(candidate)
    return names


def _text_authors(text_lines: Sequence[str], start_index: int) -> list[str]:
    """Parse the author block from the plain text lines that follow the title."""
    block: list[str] = []
    for raw in text_lines[start_index:]:
        line = raw.strip()
        if not line or _stops_authors(line) or _AFFILIATION.search(line):
            break
        if not _looks_like_author_line(line):
            break
        block.append(line)
        if len(block) >= 4:
            break
    if not block:
        return []

    names: list[str] = []
    for line in block:
        names.extend(split_names(line))
    marked = len(_MARKED_NAME.findall(" ".join(block)))
    if len(names) == 1 and marked >= 2:
        # One name out of a list of superscripted names means the separators
        # were lost; offering it would understate the author list.
        return []
    return names


def split_names(text: str) -> list[str]:
    """Comma, semicolon and ``and`` separated names, validated as names."""
    names = []
    for part in author_parts(text):
        if looks_like_name(part) and len(part.split()) <= 3:
            names.append(part)
    return names


def _looks_like_author_line(text: str) -> bool:
    return bool(_MARKED_NAME.search(text)) or _EMAIL_AT in text


def _authors_truncated(
    metadata_authors: Sequence[str], page_authors: Sequence[str]
) -> bool:
    """True when an embedded name ends where a page-1 name continues."""
    if not metadata_authors or not page_authors:
        return False
    page_words = {
        word.lower().strip(",.;") for name in page_authors for word in name.split()
    }
    for name in metadata_authors:
        last = name.split()[-1].lower().strip(",.;")
        if len(last) < 4:
            continue
        if any(word.startswith(last) and len(word) > len(last) for word in page_words):
            return True
    return False


# -- abstract and identifiers ----------------------------------------------


def _offer_abstract(layer: LayerResult, text_lines: Sequence[str]) -> None:
    """The abstract, when the page marks where it starts and ends."""
    start = None
    for index, raw in enumerate(text_lines):
        if _ABSTRACT_HEADING.match(raw):
            start = index + 1
            break
    if start is None:
        return
    body: list[str] = []
    for raw in text_lines[start:]:
        line = raw.strip()
        if not line:
            if body:
                break
            continue
        if _is_section_heading(line):
            break
        body.append(line)
    text = " ".join(" ".join(body).split())
    if len(text) < 80 or len(text.split()) < 10:
        return
    layer.offer("abstract", text, method="text:abstract-block", confidence=0.7)


def _is_section_heading(line: str) -> bool:
    """True for the heading that ends the abstract block.

    Headings are compared without their number or spacing: ``1 I NTRODUCTION``
    extracts with a space in the middle of the word.
    """
    folded = re.sub(r"[^A-Za-z]", "", line).upper()
    folded = re.sub(r"^\d+", "", folded)
    return folded in {"INTRODUCTION", "KEYWORDS", "INDEXTERMS", "RELATEDWORK"}


def _offer_identifiers(layer: LayerResult, text: str) -> None:
    arxiv = find_arxiv(text)
    if arxiv:
        layer.offer("arxiv_id", arxiv[0], method="text:arxiv-id", confidence=0.75)
        if arxiv[1]:
            layer.offer(
                "arxiv_version", arxiv[1], method="text:arxiv-id", confidence=0.75
            )
        number_year = arxiv_year(arxiv[0])
        if number_year:
            layer.offer(
                "year",
                number_year,
                method="text:arxiv-id-year",
                confidence=0.5,
            )
    doi = normalise_doi(text)
    if doi:
        layer.offer("doi", doi, method="text:doi", confidence=0.7)


def _offer_publication(
    layer: LayerResult, text_lines: Sequence[str], text: str
) -> None:
    """Journal, conference and preprint lines run as page headers or footers."""
    for raw in text_lines:
        line = raw.strip()
        match = CONFERENCE_HEADER.search(line)
        if match:
            layer.offer(
                "venue",
                f"{match.group('venue')} {match.group('year')}",
                method="text:conference-header",
                confidence=0.7,
            )
            layer.offer(
                "year",
                int(match.group("year")),
                method="text:conference-header",
                confidence=0.7,
            )
            break
        match = JOURNAL_VOLUME.match(line) or NATURE_FOOTER.match(line)
        if match:
            layer.offer(
                "venue",
                normalise_title(match.group("venue")),
                method="text:journal-line",
                confidence=0.7,
            )
            layer.offer(
                "year",
                int(match.group("year")),
                method="text:journal-line",
                confidence=0.6,
            )
            break
        match = CONFERENCE_FOOTER.search(line)
        if match:
            layer.offer(
                "venue",
                f"{match.group('venue')} {match.group('year')}",
                method="text:conference-footer",
                confidence=0.65,
            )
            layer.offer(
                "year",
                int(match.group("year")),
                method="text:conference-footer",
                confidence=0.6,
            )
            break

    published = PUBLISHED_DATE.search(text)
    if published:
        layer.offer(
            "year",
            int(published.group("year")),
            method="text:publication-date",
            confidence=0.6,
        )

    stamp = find_arxiv(text)
    if stamp:
        layer.offer("venue", "arXiv", method="text:arxiv-stamp", confidence=0.55)
        date = STAMP_DATE.search(text)
        if date:
            layer.offer(
                "year",
                int(date.group("year")),
                method="text:arxiv-stamp",
                confidence=0.6,
            )


# -- line classification ----------------------------------------------------


def _is_furniture(line: str) -> bool:
    """True for page headers, footers and kickers, which are not the title."""
    stripped = " ".join(line.split())
    if not stripped:
        return True
    if not re.search(r"[^\W\d_]", stripped):
        return True
    folded = stripped.lower().strip(" .:\u2014\u2013-")
    if folded in _KICKERS:
        return True
    # ``Article https://doi.org/10.1038/...``: a kicker in front of the DOI.
    first_word = folded.split(" ", 1)[0].rstrip(":")
    if first_word in _KICKERS and re.search(
        r"(?i)(https?://|doi:|www\.|10\.\d{4}/)", stripped
    ):
        return True
    if _FURNITURE_PREFIX.match(stripped):
        return True
    if JOURNAL_VOLUME.match(stripped) or NATURE_FOOTER.match(stripped):
        return True
    # A journal's own name, set lowercase above the title.
    return stripped.islower() and bool(_JOURNAL_NAME.match(stripped))


def _stops_title(line: str) -> bool:
    """True when a line after the title cannot be part of it."""
    if _ABSTRACT_HEADING.match(line) or _INTRODUCTION.match(line):
        return True
    if _looks_like_author_line(line):
        return True
    return bool(_AFFILIATION.search(line))


def _stops_authors(line: str) -> bool:
    if _ABSTRACT_HEADING.match(line) or _INTRODUCTION.match(line):
        return True
    if _EMAIL_AT in line and not _looks_like_author_line(line):
        return True
    # ``1Department of ...``: a numbered affiliation line.
    return line[:1].isdigit()
