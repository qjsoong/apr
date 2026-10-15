"""Command line interface.

    apr [options] FILE [FILE ...]

Reads one or more PDFs and writes JSON metadata to stdout. Never writes to the
input directory, never renames anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from apr import __version__
from apr.errors import InputError
from apr.model import STATUS_UNIDENTIFIED, PaperRecord
from apr.pipeline import identify

EXIT_OK = 0
EXIT_UNIDENTIFIED = 1
EXIT_ERROR = 2
EXIT_INTERRUPTED = 130
EXIT_BROKEN_PIPE = 141


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apr",
        description=(
            "Identify the paper in a PDF: title, authors, year, venue, DOI, "
            "arXiv ID. Metadata is written to stdout as JSON."
        ),
        epilog=(
            "Offline by default; --online also asks arXiv, Crossref, OpenAlex "
            "and Semantic Scholar. Exit codes: 0 identified, 1 unidentified, "
            "2 error."
        ),
    )
    parser.add_argument(
        "files",
        metavar="FILE",
        nargs="+",
        help="PDF file to identify; '-' reads a single PDF from stdin",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="also query online services (off by default)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="never use the network (this is the default; accepted for clarity)",
    )
    parser.add_argument(
        "--mailto",
        metavar="EMAIL",
        default=os.environ.get("APR_MAILTO") or None,
        help="contact address for the polite API pools (env: APR_MAILTO)",
    )
    parser.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=float,
        default=10.0,
        help="per-request timeout for online lookups (default: 10)",
    )
    parser.add_argument(
        "--cache-dir",
        metavar="DIR",
        type=Path,
        default=None,
        help="response cache directory (default: $XDG_CACHE_HOME/apr)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="do not read or write the response cache",
    )
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    parser.add_argument(
        "--no-abstract",
        action="store_true",
        help="omit the abstract from the output",
    )
    parser.add_argument(
        "--why",
        action="store_true",
        help="include every considered value in an 'evidence' array",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="report progress and failures on stderr",
    )
    parser.add_argument("--version", action="version", version=f"apr {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.online and args.offline:
        parser.error("--online and --offline are mutually exclusive")

    records: list[PaperRecord] = []
    had_error = False
    try:
        for spec in args.files:
            try:
                records.append(_identify_one(spec, args))
            except InputError as exc:
                print(f"apr: {exc}", file=sys.stderr)
                had_error = True
            except Exception as exc:
                # A malformed PDF is the caller's problem, not a traceback.
                print(
                    f"apr: {spec}: internal error: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                if args.verbose:
                    import traceback

                    traceback.print_exc()
                had_error = True
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED

    if records:
        try:
            sys.stdout.write(_serialise(records, args) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            # `apr f.pdf | head -1` closes the pipe; leave quietly like other tools.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            return EXIT_BROKEN_PIPE
    if had_error:
        return EXIT_ERROR
    if any(record.status == STATUS_UNIDENTIFIED for record in records):
        return EXIT_UNIDENTIFIED
    return EXIT_OK


def _identify_one(spec: str, args: argparse.Namespace) -> PaperRecord:
    if spec == "-":
        return _identify_stdin(args)
    return identify(
        spec,
        online=args.online,
        mailto=args.mailto,
        timeout=args.timeout,
        cache_dir=None if args.no_cache else args.cache_dir,
        want_abstract=not args.no_abstract,
        verbose=args.verbose,
    )


def _identify_stdin(args: argparse.Namespace) -> PaperRecord:
    """Spool stdin to a temporary file so the extractor keeps one input shape."""
    data = sys.stdin.buffer.read()
    if not data:
        raise InputError("-", "empty input")
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed, then removed
        prefix="apr-stdin-", suffix=".pdf", delete=False
    )
    try:
        with handle:
            handle.write(data)
        return _identify_one(handle.name, args)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _serialise(records: list[PaperRecord], args: argparse.Namespace) -> str:
    """One file: a JSON object. Several files: one JSON object per line."""
    payloads = [
        record.to_dict(
            include_evidence=args.why,
            include_abstract=not args.no_abstract,
            include_sources=True,
        )
        for record in records
    ]
    pretty = args.pretty
    if len(payloads) == 1:
        return json.dumps(payloads[0], ensure_ascii=False, indent=2 if pretty else None)
    if pretty:
        return json.dumps(payloads, ensure_ascii=False, indent=2)
    return "\n".join(json.dumps(payload, ensure_ascii=False) for payload in payloads)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
