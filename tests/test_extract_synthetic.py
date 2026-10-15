"""Offline extraction against the generated PDFs in ``tests/fixtures``.

The fixtures are committed and regenerable: ``uv run python
scripts/make_fixtures.py``. They cover the shapes the real corpus shows —
truncated metadata, empty metadata, a large-font title with a hyphenated line
break, two columns, and pages without a text layer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from apr.errors import InputError
from apr.extract import extract_local
from apr.extract.document import TextLine, TextSpan
from apr.extract.page import _line_text
from apr.model import PaperRecord

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Path:
    """The path of one committed fixture."""
    path = FIXTURES / name
    assert path.is_file(), f"missing fixture: {path}"
    return path


def load_generator() -> ModuleType:
    """Import ``scripts/make_fixtures.py`` by path (it is not a package)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "make_fixtures.py"
    spec = importlib.util.spec_from_file_location("make_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses in the module resolve their annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# -- the InputError contract -------------------------------------------------


def test_missing_file_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError) as raised:
        extract_local(tmp_path / "absent.pdf")
    assert raised.value.message == "no such file"
    assert raised.value.path.endswith("absent.pdf")


def test_non_pdf_bytes_are_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"this is not a PDF, it is a text file\n")
    with pytest.raises(InputError) as raised:
        extract_local(path)
    assert raised.value.message.startswith("not a PDF")


def test_empty_file_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(InputError) as raised:
        extract_local(path)
    assert raised.value.message == "empty file"


# -- file facts --------------------------------------------------------------


def test_file_facts_are_recorded() -> None:
    path = fixture("clean-metadata.pdf")
    record = extract_local(path)
    assert record.path == str(path)
    assert record.size == path.stat().st_size
    assert record.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record.pages == 1


# -- what each fixture should yield -----------------------------------------


def test_clean_metadata_fixture() -> None:
    record = extract_local(fixture("clean-metadata.pdf"))
    assert record.title == "A Clean Metadata Example"
    assert record.authors == ["Ada Lovelace", "Alan Turing"]
    assert record.doi == "10.1234/jct.2020.001"
    assert record.year == 2020
    assert record.venue == "Journal of Clean Tests"
    assert record.abstract is not None
    assert record.abstract.startswith("This fixture carries a complete /Info")
    assert record.status == "identified"
    assert record.warnings == []
    assert record.provenance["doi"].source == "pdf-info"


def test_empty_metadata_falls_back_to_the_page() -> None:
    record = extract_local(fixture("empty-metadata.pdf"))
    assert record.title == "An Empty Metadata PDF Without Any Embedded Titles"
    assert record.authors == ["Grace Hopper", "Barbara Liskov"]
    assert record.provenance["title"].source == "pdf-first-page"
    assert record.provenance["authors"].source == "pdf-first-page"
    assert record.year is None
    assert record.warnings == []


def test_truncated_metadata_loses_to_the_printed_page() -> None:
    record = extract_local(fixture("truncated-title.pdf"))
    assert record.title == (
        "A Study of Truncated Metadata in Published Articles About PDFs"
    )
    assert record.authors == ["Adrian Roitberg", "Cheng Zeng"]
    assert record.provenance["title"].source == "pdf-first-page"
    assert record.provenance["title"].confidence >= 0.85
    assert record.provenance["authors"].source == "pdf-first-page"
    assert record.warnings == [
        "embedded title looks truncated; used the title printed on page 1",
        "embedded author list looks truncated; used the authors on page 1",
    ]


def test_large_font_title_repairs_hyphenation() -> None:
    record = extract_local(fixture("large-font-title-with-hyphenation.pdf"))
    # ``MOD-`` + ``ELS`` joins to ``MODELS``, and the capitals are kept.
    assert record.title == (
        "MIN-K%++: IMPROVED BASELINE FOR DETECTING PRE-TRAINING DATA "
        "FROM LARGE LANGUAGE MODELS"
    )
    assert record.authors == ["Jingyang Zhang", "Jingwei Sun"]
    assert record.abstract is not None
    assert record.abstract.startswith("Detection of pre-training data")


def test_two_column_page() -> None:
    record = extract_local(fixture("two-column.pdf"))
    assert record.title == "A Two Column Layout Fixture"
    assert record.authors == ["Edsger Dijkstra", "Leslie Lamport"]
    assert record.abstract is not None
    assert record.abstract.startswith("The abstract is set across the full width")


def test_page_without_a_text_layer_warns_and_keeps_metadata() -> None:
    record = extract_local(fixture("no-text-layer.pdf"))
    assert record.warnings == ["no extractable text (scanned PDF?)"]
    assert record.title == "A Scanned Article Without Text"
    assert record.abstract is None


def test_text_is_looked_for_beyond_page_one() -> None:
    record = extract_local(fixture("text-on-second-page.pdf"))
    assert record.pages == 2
    assert record.warnings == ["page 1 has no text layer; used page 2"]
    assert record.title == "Text Found On The Second Page"
    assert record.authors == ["Sofia Kovalevskaya"]
    assert record.abstract is not None


# -- the rendering rules the corpus exercises --------------------------------


def test_line_text_repairs_a_small_caps_space() -> None:
    """pypdf invents a space where a small-caps word continues.

    The corpus ICLR PDF is set with two sizes per title line and no lowercase
    glyphs; pypdf reads the size change as a word break and reports
    ``I MPROVED``. A word space belongs to the larger size, so a leading space
    on a smaller run is that artefact.
    """
    line = TextLine(
        y=700.0,
        size=18.0,
        spans=(
            TextSpan(x=72.0, y=700.0, size=18.0, text="MIN-K%++: I"),
            TextSpan(x=150.0, y=700.0, size=14.4, text=" MPROVED BASELINE"),
            TextSpan(x=300.0, y=700.0, size=18.0, text=" B"),
            TextSpan(x=310.0, y=700.0, size=14.4, text="ODY"),
        ),
    )
    assert _line_text(line) == "MIN-K%++: IMPROVED BASELINE BODY"


def test_line_text_leaves_mixed_case_alone() -> None:
    line = TextLine(
        y=700.0,
        size=18.0,
        spans=(
            TextSpan(x=72.0, y=700.0, size=18.0, text="Fewer Tokens, Better Action:"),
            TextSpan(x=72.0, y=680.0, size=14.4, text=" and More"),
        ),
    )
    assert _line_text(line) == "Fewer Tokens, Better Action: and More"


# -- the fixtures and their generator ----------------------------------------


def test_committed_fixtures_match_the_generator(tmp_path: Path) -> None:
    generator = load_generator()
    written = generator.write_fixtures(tmp_path)
    assert sorted(path.name for path in written) == sorted(
        path.name for path in FIXTURES.glob("*.pdf")
    )
    for path in written:
        committed = (FIXTURES / path.name).read_bytes()
        assert path.read_bytes() == committed, (
            f"{path.name} is stale: run scripts/make_fixtures.py"
        )


def test_every_committed_fixture_is_extractable() -> None:
    records: dict[str, PaperRecord] = {
        path.name: extract_local(path) for path in sorted(FIXTURES.glob("*.pdf"))
    }
    assert records
    without_title = [name for name, record in records.items() if not record.title]
    assert without_title == []
