"""Offline extraction: what a PDF alone can tell us.

Contract (see docs/INTERFACES.md):

    extract_local(path) -> PaperRecord

Reads embedded metadata (``/Info`` and XMP), the first page's text and layout,
the file name, and anything else the file gives up, with **no network access**.
Set ``record.set_file_info(path=..., size=..., sha256=..., pages=...)``.

Each source is an evidence layer: it offers values with a confidence and the
merge rule in :class:`apr.model.PaperRecord` decides which one is kept. Layers
run weakest first, so equal confidence is resolved in favour of the source that
knows more about the paper than about the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apr.extract.document import PdfDocument
from apr.extract.filename import filename_layer
from apr.extract.layers import SOURCE_XMP, LayerResult
from apr.extract.metadata import info_layer, xmp_layer
from apr.extract.page import page_layer
from apr.model import PaperRecord

__all__ = ["extract_local"]


def extract_local(path: str | Path) -> PaperRecord:
    """Build a record from the PDF alone, without touching the network."""
    document = PdfDocument.open(path)
    record = PaperRecord()
    record.set_file_info(
        path=str(path),
        size=document.size,
        sha256=document.sha256,
        pages=document.pages,
    )

    info = info_layer(document.info)
    xmp = _xmp_layer(document)
    metadata_title = _layer_value(info, "title") or _layer_value(xmp, "title")
    metadata_authors = _layer_value(info, "authors") or _layer_value(xmp, "authors")
    page = page_layer(
        document,
        metadata_title=metadata_title if isinstance(metadata_title, str) else None,
        metadata_authors=metadata_authors if isinstance(metadata_authors, list) else [],
    )

    for layer in (filename_layer(path), info, xmp, page):
        for warning in layer.warnings:
            record.warn(warning)
        for hint in layer.hints:
            record.set_field(
                hint.field,
                hint.value,
                source=layer.source,
                method=hint.method,
                confidence=hint.confidence,
                detail=hint.detail,
            )

    _warn_about_author_lists(record, info, xmp)
    if not record.title:
        record.warn("no title found in the file")
    return record


def _xmp_layer(document: PdfDocument) -> LayerResult:
    """Read the XMP packet; a malformed one is a note, not a failure."""
    try:
        return xmp_layer(document.xmp_metadata())
    except Exception as exc:
        layer = LayerResult(source=SOURCE_XMP)
        layer.warnings.append(f"XMP metadata could not be read ({exc})")
        return layer


def _layer_value(layer: LayerResult, field: str) -> Any:
    """The value one layer offers for ``field``, strongest first."""
    best = next(
        (
            hint
            for hint in sorted(layer.hints, key=lambda hint: -hint.confidence)
            if hint.field == field
        ),
        None,
    )
    return best.value if best is not None else None


def _warn_about_author_lists(
    record: PaperRecord, info: LayerResult, xmp: LayerResult
) -> None:
    """Say so when the two embedded sources disagree about who wrote the paper."""
    info_authors = _layer_value(info, "authors")
    xmp_authors = _layer_value(xmp, "authors")
    if not isinstance(info_authors, list) or not isinstance(xmp_authors, list):
        return
    if len(info_authors) == len(xmp_authors):
        return
    record.warn(
        f"/Info lists {len(info_authors)} author(s) and XMP lists "
        f"{len(xmp_authors)}; kept the longer list"
    )
