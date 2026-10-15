#!/usr/bin/env python3
"""Write the tiny PDFs the extraction tests run against.

The files are built from raw PDF syntax so that ``apr`` keeps exactly one
runtime dependency. Regenerate them with::

    uv run python scripts/make_fixtures.py

Each page is a list of content-stream commands, usually built with :func:`text`.
Widths are a flat 500/1000 em for every glyph: the fixtures are about what a
PDF says, not about typography, and a flat table keeps pypdf's own space
guesswork predictable between the fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
PAGE_WIDTH = 612
PAGE_HEIGHT = 792
_FONT_OBJECTS = {
    "F1": "/BaseFont /Helvetica",
    "F2": "/BaseFont /Times-Roman",
}


@dataclass
class Page:
    """One page: content-stream commands plus the fonts they may use."""

    commands: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=lambda: ["F1"])

    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: float = 12.0,
        font: str = "F1",
    ) -> Page:
        if font not in self.fonts:
            self.fonts.append(font)
        self.commands.append(
            f"BT /{font} {size:g} Tf 1 0 0 1 {x:g} {y:g} Tm ({escape(text)}) Tj ET"
        )
        return self

    def rectangle(self, x: float, y: float, width: float, height: float) -> Page:
        self.commands.append(f"0.8 g {x:g} {y:g} {width:g} {height:g} re f 0 g")
        return self

    def paragraph(
        self,
        x: float,
        y: float,
        lines: list[str],
        *,
        size: float = 10.0,
        leading: float = 12.0,
    ) -> Page:
        for index, line in enumerate(lines):
            self.text(x, y - index * leading, line, size=size)
        return self


def escape(text: str) -> str:
    """Escape a string for a PDF literal, dropping anything not WinAnsi."""
    out = []
    for char in text:
        if char in "()\\":
            out.append("\\" + char)
        elif 32 <= ord(char) <= 126:
            out.append(char)
        else:
            out.append(f"\\{ord(char):03o}")
    return "".join(out)


def build_pdf(pages: list[Page], info: dict[str, str] | None = None) -> bytes:
    """Assemble a PDF with a correct cross-reference table."""
    objects: list[bytes] = []
    fonts = sorted({name for page in pages for name in page.fonts})
    # 1 catalog, 2 pages, one object per page and per content stream, then fonts.
    font_numbers = {
        name: 3 + 2 * len(pages) + index for index, name in enumerate(fonts)
    }
    info_number = 3 + 2 * len(pages) + len(fonts)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + index} 0 R" for index in range(len(pages)))
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii")
    )
    for index, page in enumerate(pages):
        content_number = 3 + len(pages) + index
        resources = " ".join(
            f"/{name} {font_numbers[name]} 0 R" for name in sorted(page.fonts)
        )
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R"
                f" /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}]"
                f" /Resources << /Font << {resources} >> >>"
                f" /Contents {content_number} 0 R >>"
            ).encode("ascii")
        )
    for page in pages:
        stream = "\n".join(page.commands).encode("ascii")
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    for name in fonts:
        objects.append(
            (
                f"<< /Type /Font /Subtype /Type1 {_FONT_OBJECTS[name]}"
                " /Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 126 /Widths ["
                + " ".join(["500"] * 95)
                + "] >>"
            ).encode("ascii")
        )
    objects.append(_info_object(info).encode("ascii"))

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_number} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def _info_object(info: dict[str, str] | None) -> str:
    entries = "".join(
        f" /{key} ({escape(value)})" for key, value in (info or {}).items()
    )
    return f"<<{entries} >>"


# -- the fixtures ------------------------------------------------------------


def clean_metadata() -> list[Page]:
    page = Page()
    page.text(72, 700, "A Clean Metadata Example", size=20)
    page.text(72, 672, "Ada Lovelace1, Alan Turing2", size=11)
    page.text(72, 660, "1Analytical Engine Lab, 2Bletchley Park", size=9)
    page.text(72, 630, "Abstract", size=11)
    page.paragraph(
        72,
        615,
        [
            "This fixture carries a complete /Info dictionary: title, authors,",
            "subject with a DOI and a creation date. The page repeats the same",
            "facts so that extraction can cross-check the two sources.",
        ],
    )
    page.text(72, 520, "1 Introduction", size=11)
    page.paragraph(72, 500, ["Body text follows the abstract heading."], size=10)
    page.text(72, 60, "Journal of Clean Tests | Volume 3 | March 2020 | 1-10", size=9)
    return [page]


CLEAN_METADATA_INFO = {
    "Title": "A Clean Metadata Example",
    "Author": "Ada Lovelace; Alan Turing",
    "Subject": "Journal of Clean Tests, doi:10.1234/jct.2020.001",
    "CreationDate": "D:20200101000000Z",
}


def empty_metadata() -> list[Page]:
    page = Page()
    page.text(72, 700, "An Empty Metadata PDF Without Any Embedded Titles", size=18)
    page.text(72, 670, "Grace Hopper1, Barbara Liskov1", size=11)
    page.text(72, 658, "1Navy Research Lab", size=9)
    page.text(72, 630, "Abstract", size=11)
    page.paragraph(
        72,
        615,
        [
            "The producer wrote no information dictionary at all, so every fact",
            "has to come from the printed page. This is common for PDFs exported",
            "from a word processor's draft mode.",
        ],
    )
    page.text(72, 520, "1 Introduction", size=11)
    return [page]


def truncated_metadata() -> list[Page]:
    page = Page()
    page.text(
        72,
        700,
        "A Study of Truncated Metadata in Published Articles About PDFs",
        size=16,
    )
    page.text(72, 672, "Adrian Roitberg, Cheng Zeng", size=11)
    page.text(72, 660, "1Department of Chemistry, University of Florida", size=9)
    page.text(72, 630, "Abstract", size=11)
    page.paragraph(
        72,
        615,
        [
            "Some producers cut a long title at a fixed length and store the",
            "result in /Title, which leaves a value that ends mid-word.",
        ],
    )
    page.text(72, 520, "1 Introduction", size=11)
    return [page]


TRUNCATED_METADATA_INFO = {
    # Both values are cut exactly as the corpus files are.
    "Title": "A Study of Truncated Metadata in Published Articles About PD",
    "Author": "Adrian Roitbe",
}


def hyphenated_title() -> list[Page]:
    """A title set large, with a word broken across the last two lines.

    The closing fragment is in a smaller size, as a small-caps title would be,
    which is what makes the title run continue past it.
    """
    page = Page()
    page.text(72, 700, "MIN-K%++: IMPROVED BASELINE", size=18)
    page.text(72, 678, "FOR DETECTING PRE-TRAINING DATA", size=18)
    page.text(72, 656, "FROM LARGE LANGUAGE MOD-", size=18)
    page.text(72, 634, "ELS", size=14)
    page.text(72, 610, "Jingyang Zhang1, Jingwei Sun1", size=10)
    page.text(72, 598, "1Duke University", size=9)
    page.text(72, 570, "ABSTRACT", size=10)
    page.paragraph(
        72,
        556,
        [
            "Detection of pre-training data is measured with a baseline that",
            "combines several token-level statistics into one score.",
        ],
        size=9,
    )
    page.text(72, 500, "1 INTRODUCTION", size=10)
    return [page]


def two_column() -> list[Page]:
    page = Page()
    page.text(72, 700, "A Two Column Layout Fixture", size=20)
    page.text(72, 672, "Edsger Dijkstra1, Leslie Lamport1", size=11)
    page.text(72, 660, "1Centrum Wiskunde & Informatica", size=9)
    page.text(72, 630, "Abstract", size=11)
    page.paragraph(
        72,
        615,
        [
            "The abstract is set across the full width of the page, while the",
            "body below it runs in two independent columns.",
        ],
    )
    page.text(72, 560, "1 Introduction", size=11)
    body = [
        "The left column begins the introduction and continues",
        "below this line with further prose about the fixture.",
        "Each column is a vertical run of text that shares its",
        "baseline with the other one.",
    ]
    page.paragraph(72, 540, body, size=10)
    page.paragraph(330, 540, body, size=10)
    return [page]


def no_text_layer() -> list[Page]:
    page = Page()
    page.rectangle(72, 300, 460, 300)
    return [page]


def text_on_second_page() -> list[Page]:
    first = Page()
    first.rectangle(72, 300, 460, 300)
    second = Page()
    second.text(72, 700, "Text Found On The Second Page", size=18)
    second.text(72, 670, "Sofia Kovalevskaya1", size=11)
    second.text(72, 658, "1Stockholm University", size=9)
    second.text(72, 630, "Abstract", size=11)
    second.paragraph(
        72,
        615,
        [
            "A scanned cover page carries no text layer, but the article itself",
            "was exported with one.",
        ],
    )
    second.text(72, 520, "1 Introduction", size=11)
    return [first, second]


FIXTURES: dict[str, tuple[list[Page], dict[str, str] | None]] = {
    "clean-metadata.pdf": (clean_metadata(), CLEAN_METADATA_INFO),
    "empty-metadata.pdf": (empty_metadata(), None),
    "truncated-title.pdf": (truncated_metadata(), TRUNCATED_METADATA_INFO),
    "large-font-title-with-hyphenation.pdf": (hyphenated_title(), None),
    "two-column.pdf": (two_column(), None),
    "no-text-layer.pdf": (
        no_text_layer(),
        {"Title": "A Scanned Article Without Text", "Author": "Anonymous"},
    ),
    "text-on-second-page.pdf": (
        text_on_second_page(),
        {"Title": "A Scanned Cover Page", "Author": "Sofia Kovalevskaya"},
    ),
}


def write_fixtures(directory: Path = FIXTURE_DIR) -> list[Path]:
    """Write every fixture and return the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (pages, info) in sorted(FIXTURES.items()):
        path = directory / name
        path.write_bytes(build_pdf(pages, info))
        written.append(path)
    return written


def main() -> None:
    for path in write_fixtures():
        relative = path.relative_to(FIXTURE_DIR.parent.parent)
        print(f"wrote {relative} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
