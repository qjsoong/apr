#!/usr/bin/env python3
"""Measure apr against real PDFs and verified ground truth.

The corpus is a local directory of papers (default ``~/doc/paper``, override with
``--corpus`` or ``APR_CORPUS``); it is copyrighted and never distributed with the
repository. The expectations in ``tests/corpus/expected.json`` were verified
against arXiv, Crossref, DBLP and publisher pages, independently of the code
being measured here.

Comparison is deliberately dumb and independent of apr's own matching code:
titles are casefolded, accent-folded, punctuation-flattened and compared for
equality, with a similarity ratio reported alongside.

    uv run python scripts/eval_corpus.py                 # offline, the default
    uv run python scripts/eval_corpus.py --online
    uv run python scripts/eval_corpus.py --json > out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPECTED = REPO / "tests" / "corpus" / "expected.json"

# The bars agreed in notes/CONTRACT.md. Floors, not targets.
BARS = {
    "offline": {"title_exact": 9, "author_surname": 9, "title_nonempty": 12},
    "online": {"title_exact": 11, "author_surname": 11},
}


# Spacing accents left over from LaTeX and from letterspaced PDF text (as in the
# Polish surname with a stray acute over the n) sit inside a word and must not
# turn into a word boundary when the accent itself is folded away.
_SPACING_ACCENTS = "\u00b4`^~\u00a8\u02dc\u02c7\u02ca\u02cb\u2032\u2035\u00b8"


def normalise(text: str | None) -> str:
    if not text:
        return ""
    text = text.translate({ord(ch): None for ch in _SPACING_ACCENTS})
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = re.sub(r"[^0-9a-zA-Z]+", " ", folded.casefold())
    return " ".join(folded.split())


def compact(text: str | None) -> str:
    """Normalised with every space removed: diagnostic for layout artefacts."""
    return normalise(text).replace(" ", "")


def similarity(left: str | None, right: str | None) -> float:
    return SequenceMatcher(None, normalise(left), normalise(right)).ratio()


def surname(name: str | None) -> str:
    parts = normalise(name).split()
    return parts[-1] if parts else ""


def first_surname(names: list[str] | None) -> str:
    return surname(names[0]) if names else ""


def run_apr(
    path: Path, *, online: bool, timeout: float
) -> tuple[dict | None, str, float]:
    command = [sys.executable, "-m", "apr", "--no-abstract", str(path)]
    if online:
        command.insert(3, "--online")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, cwd=REPO
        )
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout:g}s", time.monotonic() - started
    elapsed = time.monotonic() - started
    if not proc.stdout.strip():
        return None, (proc.stderr.strip() or "no output"), elapsed
    try:
        return json.loads(proc.stdout), proc.stderr.strip(), elapsed
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}", elapsed


def title_match(expected: dict, value: str | None) -> tuple[bool, str | None]:
    """Does the title match the authority's wording, or a documented alternate?

    Returns (matched, source of the alternate). A match on the primary wording
    returns (True, None). An alternate exists for the rare case where two
    authorities print the same paper's title differently - the same paper is
    still identified - and each one is recorded with the URL that prints it.
    """
    target = normalise(value)
    if not target:
        return False, None
    if normalise(expected["title"]) == target:
        return True, None
    for source, alternate in (expected.get("title_alternates") or {}).items():
        if normalise(alternate) == target:
            return True, source
    return False, None


def compare(expected: dict, actual: dict | None) -> dict:
    matched, matched_via = title_match(expected, (actual or {}).get("title"))
    if actual is None:
        return {
            "title_nonempty": False,
            "title_exact": False,
            "title_exact_ignoring_spaces": False,
            "matched_via": None,
            "title_similarity": 0.0,
            "author_surname": False,
            "doi_exact": False,
            "arxiv_exact": False,
            "year_exact": False,
            "status": None,
            "confidence": None,
        }
    known_doi = expected.get("doi")
    known_arxiv = expected.get("arxiv_id")
    return {
        "title_nonempty": bool(actual.get("title")),
        "title_exact": matched,
        "title_exact_ignoring_spaces": compact(actual.get("title"))
        == compact(expected["title"]),
        "matched_via": matched_via,
        "title_similarity": round(
            similarity(actual.get("title"), expected["title"]), 3
        ),
        "author_surname": first_surname(actual.get("authors"))
        == first_surname(expected["authors"]),
        "doi_exact": normalise(actual.get("doi")) == normalise(known_doi)
        if known_doi
        else None,
        "arxiv_exact": (actual.get("arxiv_id") or "") == (known_arxiv or "")
        if known_arxiv
        else None,
        "year_exact": actual.get("year") == expected.get("year"),
        "status": actual.get("status"),
        "confidence": actual.get("confidence"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(os.environ.get("APR_CORPUS", "~/doc/paper")).expanduser(),
        help="directory of PDFs (default: $APR_CORPUS or ~/doc/paper)",
    )
    parser.add_argument("--online", action="store_true", help="measure --online")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--only", metavar="SUBSTRING", help="restrict to matching files"
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="per-file timeout (default 60s)"
    )
    args = parser.parse_args()

    if not EXPECTED.exists():
        print(f"missing ground truth: {EXPECTED}", file=sys.stderr)
        return 2
    if not args.corpus.is_dir():
        print(f"corpus directory not found: {args.corpus}", file=sys.stderr)
        return 2

    ground_truth = json.loads(EXPECTED.read_text(encoding="utf-8"))
    files: dict[str, dict] = ground_truth["files"]
    mode = "online" if args.online else "offline"
    bars = BARS[mode]

    rows: list[dict] = []
    for name, expected in sorted(files.items()):
        if args.only and args.only not in name:
            continue
        path = args.corpus / name
        if not path.exists():
            rows.append(
                {
                    "file": name,
                    "missing": True,
                    "error": "not in corpus",
                    **compare(expected, None),
                }
            )
            continue
        actual, error, elapsed = run_apr(path, online=args.online, timeout=args.timeout)
        rows.append(
            {
                "file": name,
                "missing": False,
                "error": error or None,
                "seconds": round(elapsed, 2),
                "expected_title": expected["title"],
                "actual_title": (actual or {}).get("title"),
                "expected_authors": len(expected.get("authors") or []),
                "actual_authors": len((actual or {}).get("authors") or []),
                "sources": [
                    {"source": item.get("source"), "status": item.get("status")}
                    for item in (actual or {}).get("sources") or []
                ],
                "warnings": (actual or {}).get("warnings") or [],
                **compare(expected, actual),
            }
        )

    present = [row for row in rows if not row["missing"]]
    totals = {
        "files": len(present),
        "title_nonempty": sum(1 for row in present if row["title_nonempty"]),
        "title_exact": sum(1 for row in present if row["title_exact"]),
        "title_exact_ignoring_spaces": sum(
            1 for row in present if row["title_exact_ignoring_spaces"]
        ),
        "author_surname": sum(1 for row in present if row["author_surname"]),
        "mean_title_similarity": round(
            sum(row["title_similarity"] for row in present) / len(present), 3
        )
        if present
        else 0.0,
        "network_attempts": sum(len(row["sources"]) for row in present),
        "matched_via_alternate": sum(1 for row in present if row["matched_via"]),
        "unreported_attempts": sum(
            1
            for row in present
            if row["sources"] and not row["sources"][0].get("status")
        ),
    }
    verdicts = {key: totals.get(key, 0) >= floor for key, floor in bars.items()}
    # The parent's must-have: the truncated-metadata case must yield the full title.
    pinned = next(
        (row for row in present if row["file"].startswith("2610.01939")), None
    )
    verdicts["truncated_metadata_case"] = bool(pinned and pinned["title_exact"])

    if args.json:
        print(
            json.dumps(
                {
                    "mode": mode,
                    "corpus": str(args.corpus),
                    "verified_at": ground_truth.get("verified_at"),
                    "bars": bars,
                    "totals": totals,
                    "verdicts": verdicts,
                    "passed": all(verdicts.values()),
                    "rows": rows,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if all(verdicts.values()) else 1

    width = max((len(row["file"]) for row in rows), default=10)
    print(f"apr corpus evaluation - mode: {mode} - corpus: {args.corpus}")
    print("-" * (width + 78))
    header = (
        f"{'file'.ljust(width)}  {'title':>5} {'sim':>5} {'author':>6} {'doi':>4} "
        f"{'arxiv':>5} {'year':>4} {'status':>11} {'conf':>4} {'s':>5}"
    )
    print(header)
    for row in rows:

        def mark(value: bool | None) -> str:
            return "-" if value is None else ("ok" if value else "FAIL")

        print(
            f"{row['file'].ljust(width)}  {mark(row['title_exact']):>5} "
            f"{row['title_similarity']:>5} {mark(row['author_surname']):>6} "
            f"{mark(row['doi_exact']):>4} {mark(row['arxiv_exact']):>5} "
            f"{mark(row['year_exact']):>4} {row['status'] or '-'!s:>11} "
            f"{row['confidence'] if row['confidence'] is not None else '-'!s:>4} "
            f"{row.get('seconds', '-')!s:>5}"
        )
        if row.get("error"):
            print(f"{'':{width}}    error: {row['error']}")
        if row["matched_via"]:
            print(f"{'':{width}}    title matched via alternate: {row['matched_via']}")
        if row["title_exact"] is False and row.get("actual_title"):
            print(f"{'':{width}}    got:  {row['actual_title']}")
            print(f"{'':{width}}    want: {row['expected_title']}")
    print("-" * (width + 78))
    for key, floor in bars.items():
        got = totals.get(key, 0)
        print(
            f"bar {key:<16} {got}/{totals['files']} (floor {floor}) "
            f"{'PASS' if verdicts[key] else 'FAIL'}"
        )
    print(
        f"bar truncated_metadata_case "
        f"{'PASS' if verdicts['truncated_metadata_case'] else 'FAIL'}"
    )
    print(
        f"totals: titles {totals['title_exact']}/{totals['files']} exact "
        f"({totals['title_exact_ignoring_spaces']}/{totals['files']} ignoring "
        f"spaces, {totals['mean_title_similarity']} mean similarity), "
        f"first authors {totals['author_surname']}/{totals['files']}, "
        f"titles matched via a documented alternate "
        f"{totals['matched_via_alternate']}, "
        f"network attempts reported {totals['network_attempts']}"
    )
    print("verdict:", "PASS" if all(verdicts.values()) else "FAIL")
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
