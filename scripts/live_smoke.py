#!/usr/bin/env python
"""Live smoke test for online resolution, against the real endpoints.

This script is the only thing in the repository that intentionally reaches the
network outside the marked ``network`` tests, so it runs only when
``APR_TEST_ALLOW_NETWORK=1`` is set:

    APR_TEST_ALLOW_NETWORK=1 uv run python scripts/live_smoke.py

It resolves a fixed list of papers — arXiv ids, DOIs and two bibliographic
lookups — and prints one row per case: the accepted title, first author,
identifiers, the provider whose answer won, and the score behind it. Every
attempt is echoed underneath, so refusals, rate limits and query shapes are
visible rather than hidden by a plausible-looking row.

The records below stand in for what offline extraction hands to ``resolve``:
titles and author names as printed on the first page, identifiers as extracted.
Responses are cached under ``.cache/live-smoke`` by default; pass ``--no-cache``
to ask the services afresh.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from apr.model import PaperRecord
from apr.providers import resolve

CONFIDENT_MATCH = 0.85

#: arXiv id and the version the file name carries: the API answers with the
#: latest version, and the file's own version decides which date the year is.
ARXIV_IDS = (
    ("2306.12983", "v2"),
    ("2310.16789", "v3"),
    ("2505.21469", "v4"),
    ("2610.01939", "v2"),
)

DOIS = (
    "10.1038/s41467-024-53632-4",
    "10.1038/s41467-025-60763-9",
    "10.1038/s41467-026-75702-5",
    "10.1038/s43588-024-00737-x",
    "10.1038/s43588-026-00986-y",
)


@dataclass(slots=True)
class Local:
    """What offline extraction supplies for one bibliographic case.

    Title and first author as printed on page one, plus the venue and year the
    page's own conference header, journal line or XMP gives - the local values a
    preprint match must not overwrite.
    """

    title: str
    author: str
    venue: str
    year: int
    venue_method: str
    venue_confidence: float
    year_confidence: float


#: The two conference papers in ~/doc/paper, with the layers that identify them.
BIBLIOGRAPHIC = (
    Local(
        title=(
            "Towards More Realistic Membership Inference Attacks on Large "
            "Diffusion Models"
        ),
        author="Jan Dubinski",
        venue="IEEE Winter Conference on Applications of Computer Vision",
        year=2024,
        venue_method="xmp:dc:description",
        venue_confidence=0.65,
        year_confidence=0.4,  # from the file name: ..._WACV_2024_paper.pdf
    ),
    Local(
        title="Attention Is All You Need",
        author="Ashish Vaswani",
        venue="NIPS 2017",
        year=2017,
        venue_method="text:conference-footer",
        venue_confidence=0.65,
        year_confidence=0.6,
    ),
)


@dataclass(slots=True)
class Case:
    """One record to resolve, and the label it is reported under."""

    label: str
    record: PaperRecord


def cases() -> list[Case]:
    found: list[Case] = []
    for arxiv_id, version in ARXIV_IDS:
        record = PaperRecord(path=f"{arxiv_id}{version}.pdf")
        record.set_field(
            "arxiv_id",
            arxiv_id,
            source="pdf-first-page",
            method="text:arxiv-id",
            confidence=0.75,
        )
        record.set_field(
            "arxiv_version",
            version,
            source="pdf-first-page",
            method="text:arxiv-id",
            confidence=0.75,
        )
        found.append(Case(f"arXiv {arxiv_id}{version}", record))
    for doi in DOIS:
        record = PaperRecord(path=f"{doi.replace('/', '_')}.pdf")
        record.set_field(
            "doi", doi, source="pdf-info", method="info:doi", confidence=0.7
        )
        found.append(Case(f"DOI {doi}", record))
    for local in BIBLIOGRAPHIC:
        record = PaperRecord(path=f"{local.title[:40]}.pdf")
        record.set_field(
            "title",
            local.title,
            source="pdf-first-page",
            method="layout",
            confidence=0.7,
        )
        record.set_field(
            "authors",
            [local.author],
            source="pdf-first-page",
            method="layout",
            confidence=0.6,
        )
        record.set_field(
            "venue",
            local.venue,
            source="pdf-first-page",
            method=local.venue_method,
            confidence=local.venue_confidence,
        )
        record.set_field(
            "year",
            local.year,
            source="filename",
            method="name:year",
            confidence=local.year_confidence,
        )
        found.append(Case(f"title {local.title[:30]}…", record))
    return found


def field(record: PaperRecord, name: str) -> str:
    value = getattr(record, name)
    if value in (None, "", []):
        return "—"
    if isinstance(value, list):
        return str(value[0])
    return str(value)


def identifiers(record: PaperRecord) -> str:
    parts = []
    if record.doi:
        parts.append(f"doi:{record.doi}")
    if record.arxiv_id:
        parts.append(f"arXiv:{record.arxiv_id}{record.arxiv_version or ''}")
    return " ".join(parts) or "—"


def score_of(record: PaperRecord) -> str:
    """The score behind the accepted title.

    Identifier lookups are authoritative by contract (confidence 1.0, not
    scored); a search hit was offered at ``0.5 + 0.5 * score``, so the score is
    recoverable from the confidence.
    """
    provenance = record.provenance.get("title")
    if provenance is None:
        return "—"
    confidence = provenance.confidence
    return f"{confidence:.2f}" if confidence == 1.0 else f"{2 * confidence - 1:.2f}"


def row(case: Case, record: PaperRecord) -> str:
    provenance = record.provenance.get("title")
    provider = provenance.source if provenance else "—"
    title = field(record, "title")
    if len(title) > 52:
        title = title[:51] + "…"
    venue = field(record, "venue")
    if len(venue) > 40:
        venue = venue[:39] + "…"
    return (
        f"| {case.label} | {provider} | {score_of(record)} | {title} | "
        f"{field(record, 'authors')} | {field(record, 'year')} | {venue} | "
        f"{identifiers(record)} |"
    )


def attempts(record: PaperRecord) -> str:
    lines = []
    for attempt in record.sources:
        detail = f"{attempt.source} {attempt.status}"
        if attempt.candidates is not None:
            detail += f" ({attempt.candidates} candidates)"
        if attempt.query:
            detail += f" [{attempt.query[:70]}]"
        if attempt.error:
            detail += f" {attempt.error}"
        lines.append(detail)
    return "; ".join(lines) or "no source attempted"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mailto",
        default=os.environ.get("APR_MAILTO") or None,
        help="contact address for the polite API pools (env: APR_MAILTO)",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".cache" / "live-smoke",
    )
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)

    if os.environ.get("APR_TEST_ALLOW_NETWORK") != "1":
        print(
            "refusing to run: this script makes live network requests. "
            "Set APR_TEST_ALLOW_NETWORK=1 to allow them.",
            file=sys.stderr,
        )
        return 2

    cache_dir = None if args.no_cache else args.cache_dir
    print(
        f"# apr live smoke: {len(cases())} lookups, "
        f"mailto={args.mailto or 'none'}, timeout={args.timeout}s, "
        f"cache={cache_dir or 'disabled'}"
    )
    print()
    print(
        "| case | provider | score | title | first author | year | venue "
        "| identifiers |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")

    unresolved: list[str] = []
    lines: list[tuple[Case, PaperRecord]] = []
    for case in cases():
        record = resolve(
            case.record,
            mailto=args.mailto,
            timeout=args.timeout,
            cache_dir=cache_dir,
            verbose=False,
        )
        lines.append((case, record))
        print(row(case, record))

    print()
    print("## Attempts")
    print()
    for case, record in lines:
        print(f"- **{case.label}**: {attempts(record)}")
        for warning in record.warnings:
            print(f"  - warning: {warning}")
        if not record.title:
            unresolved.append(case.label)

    print()
    if unresolved:
        print(f"UNRESOLVED: {', '.join(unresolved)}")
        return 1
    print("Every case resolved to a title.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
