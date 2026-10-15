"""Reading a PDF offline: file facts, page text and page layout spans."""

from __future__ import annotations

import hashlib
import io
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from apr.errors import InputError

# pypdf logs a warning for every glyph it cannot map through fontTools. Those
# warnings are noise for a tool whose stderr should stay empty on success.
logging.getLogger("pypdf").setLevel(logging.ERROR)

#: How many pages are searched for a text layer before calling a PDF scanned.
TEXT_PAGE_LIMIT = 3


@dataclass(frozen=True, slots=True)
class TextSpan:
    """One run of characters as pypdf decoded it, with its position and size."""

    x: float
    y: float
    size: float
    text: str


@dataclass(frozen=True, slots=True)
class TextLine:
    """Spans that share a baseline, ordered left to right."""

    y: float
    size: float
    spans: tuple[TextSpan, ...]

    @property
    def raw(self) -> str:
        """The line as plain text, with a trailing newline removed."""
        return "".join(span.text for span in self.spans).rstrip("\n")


class PdfDocument:
    """A PDF opened for reading, with its bytes hashed and its pages available."""

    def __init__(self, path: Path, data: bytes) -> None:
        self.path = path
        self.size = len(data)
        self.sha256 = hashlib.sha256(data).hexdigest()
        self._reader = PdfReader(io.BytesIO(data), strict=False)
        self.pages = len(self._reader.pages)

    @classmethod
    def open(cls, path: str | Path) -> PdfDocument:
        """Read ``path``; raise :class:`InputError` if it is not a readable PDF."""
        resolved = Path(path)
        try:
            data = resolved.read_bytes()
        except FileNotFoundError as exc:
            raise InputError(str(path), "no such file") from exc
        except IsADirectoryError as exc:
            raise InputError(str(path), "is a directory") from exc
        except PermissionError as exc:
            raise InputError(str(path), "permission denied") from exc
        except OSError as exc:
            raise InputError(str(path), f"cannot be read ({exc.strerror})") from exc
        if not data:
            raise InputError(str(path), "empty file")

        try:
            document = cls(resolved, data)
            if document._reader.is_encrypted and not document._reader.decrypt(""):
                raise InputError(str(path), "encrypted PDF (a password is required)")
        except InputError:
            raise
        except Exception as exc:  # pypdf raises a family of parser errors
            raise InputError(str(path), f"not a PDF ({exc})") from exc
        return document

    # -- pages ---------------------------------------------------------------

    @property
    def info(self) -> dict[str, Any]:
        """The document information dictionary (``/Info``), possibly empty."""
        try:
            return dict(self._reader.metadata or {})
        except Exception:  # a malformed /Info must not sink the extraction
            return {}

    def xmp_metadata(self) -> Any:
        """The XMP packet, or None. Raises if the packet cannot be parsed."""
        return self._reader.xmp_metadata

    def page(self, index: int) -> Any:
        """The page object at ``index``, raising InputError when unreadable."""
        try:
            return self._reader.pages[index]
        except InputError:
            raise
        except Exception as exc:
            raise InputError(
                str(self.path), f"page {index + 1} is unreadable ({exc})"
            ) from exc

    def page_height(self, index: int) -> float:
        """The page's height in points, used to tell the top of the page."""
        try:
            return float(self.page(index).mediabox.height)
        except Exception:  # a page without a usable /MediaBox
            return 792.0

    def page_text(self, index: int) -> tuple[str, list[TextSpan]]:
        """Extract text and layout spans for one page in a single pass.

        pypdf hands the same chunks to ``visitor_text`` that it would have
        returned, so joining them reproduces ``extract_text()`` exactly.
        """
        chunks: list[str] = []
        spans: list[TextSpan] = []

        def visit(
            text: str,
            cm: Sequence[float],
            tm: Sequence[float],
            font_dict: Any,
            font_size: float,
        ) -> None:
            if not text:
                return
            chunks.append(text)
            if not text.strip():
                return
            scale = math.hypot(float(tm[0]), float(tm[1])) * math.hypot(
                float(cm[0]), float(cm[1])
            )
            spans.append(
                TextSpan(
                    x=float(cm[0]) * float(tm[4])
                    + float(cm[2]) * float(tm[5])
                    + float(cm[4]),
                    y=float(cm[1]) * float(tm[4])
                    + float(cm[3]) * float(tm[5])
                    + float(cm[5]),
                    size=abs(float(font_size)) * (scale or 1.0),
                    text=text,
                )
            )

        page = self.page(index)
        try:
            page.extract_text(visitor_text=visit)
        except Exception as exc:
            raise InputError(
                str(self.path), f"page {index + 1} text is unreadable ({exc})"
            ) from exc
        return "".join(chunks), spans

    def page_lines(self, index: int) -> list[TextLine]:
        """Layout lines of one page, top row first."""
        _, spans = self.page_text(index)
        return layout_lines(spans)

    def text_page(self) -> tuple[int, str, list[TextSpan]]:
        """The first of the first pages that carries a text layer.

        Returns ``(index, text, spans)``; the index is -1 when no page in the
        limit has any text (a scanned PDF, which apr does not OCR).
        """
        for index in range(min(self.pages, TEXT_PAGE_LIMIT)):
            text, spans = self.page_text(index)
            if text.strip():
                return index, text, spans
        return -1, "", []


def layout_lines(spans: Sequence[TextSpan]) -> list[TextLine]:
    """Group spans into lines by baseline.

    Spans that sit a little above a line (superscripts) belong to it, so the
    tolerance follows the page's typical text size rather than the row it is
    currently building.
    """
    if not spans:
        return []
    sizes = sorted(span.size for span in spans)
    median = sizes[len(sizes) // 2]
    tolerance = max(1.5, 0.5 * median)

    ordered = sorted(spans, key=lambda span: -span.y)
    groups: list[list[TextSpan]] = []
    anchor = ordered[0].y
    for span in ordered:
        if groups and anchor - span.y <= tolerance:
            groups[-1].append(span)
            anchor = max(anchor, span.y)
            continue
        groups.append([span])
        anchor = span.y

    lines = []
    for group in groups:
        # Reading order within a line is left to right. Spans whose position
        # pypdf could not refresh keep their document order (stable sort).
        ordered_group = tuple(sorted(group, key=lambda span: span.x))
        lines.append(
            TextLine(
                y=max(span.y for span in group),
                size=max(span.size for span in group),
                spans=ordered_group,
            )
        )
    lines.sort(key=lambda line: -line.y)
    return lines
