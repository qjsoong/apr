"""Online resolution: ask scholarly APIs which paper this is.

Contract (see docs/INTERFACES.md):

    resolve(record, *, mailto, timeout, cache_dir, verbose) -> PaperRecord

Takes the record produced by local extraction, looks it up by identifier or by
bibliographic search, and offers every field it finds through
``record.set_field(...)`` so confidence decides the winner. Failures are
recorded as ``record.add_source(SourceAttempt(..., status=SOURCE_ERROR))``;
they never raise.
"""

from __future__ import annotations

from pathlib import Path

from apr.model import PaperRecord


def resolve(
    record: PaperRecord,
    *,
    mailto: str | None = None,
    timeout: float = 10.0,
    cache_dir: Path | None = None,
    verbose: bool = False,
) -> PaperRecord:
    """Enrich a locally extracted record using online services."""
    raise NotImplementedError("online resolution is implemented in a later milestone")
