"""Offline extraction: what a PDF alone can tell us.

Contract (see docs/INTERFACES.md):

    extract_local(path) -> PaperRecord

Reads embedded metadata (``/Info`` and XMP), the first page's text and layout,
the file name, and anything else the file gives up, with **no network access**.
Set ``record.set_file_info(path=..., size=..., sha256=..., pages=...)``.
"""

from __future__ import annotations

from pathlib import Path

from apr.model import PaperRecord


def extract_local(path: str | Path) -> PaperRecord:
    """Build a record from the PDF alone."""
    raise NotImplementedError("offline extraction is implemented in the next milestone")
