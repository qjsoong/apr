"""Orchestration: local extraction first, optional network resolution after it."""

from __future__ import annotations

from pathlib import Path

from apr.extract import extract_local
from apr.model import PaperRecord
from apr.providers import resolve


def identify(
    path: str | Path,
    *,
    online: bool = False,
    mailto: str | None = None,
    timeout: float = 10.0,
    cache_dir: Path | None = None,
    want_abstract: bool = True,
    verbose: bool = False,
) -> PaperRecord:
    """Identify the paper in one PDF.

    Offline by default: the record comes from the file alone. With ``online``
    the identifiers and text found locally are used to look the paper up in
    arXiv, Crossref, OpenAlex and Semantic Scholar, and the better-evidenced
    values win per field.
    """
    record = extract_local(path)
    if online:
        record = resolve(
            record,
            mailto=mailto,
            timeout=timeout,
            cache_dir=cache_dir,
            verbose=verbose,
        )
    if not want_abstract:
        record.abstract = None
        record.provenance.pop("abstract", None)
    return record
