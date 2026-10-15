# Contract: apr 0.1.0

Recorded before implementation, from the brief and the answers to the Phase 0
batch. Reviewers check delivery against this file.

## Goal

Identify which paper a PDF is, and report its metadata as JSON on stdout, so
that other programs can rename files, build citations or deduplicate.

## Identity

- **Is:** a single-purpose Unix filter. PDF path in, JSON metadata out.
- **Is not:** a reference manager, a renamer, a downloader, a library/database, a
  BibTeX formatter, an OCR tool, a GUI. It never writes to the input directory.
- **Input:** one or more local PDF paths, or `-` for one PDF on stdin. URLs and
  bare DOIs are not accepted as input.

## Audience

Unknown strangers on the internet: a public GitHub repository, MIT licensed,
documented install path, CI on the supported Python versions. No PyPI
publication in this run (deliberately deferred, recorded in the README).

## Must-haves

| Must-have | Check |
| --- | --- |
| Offline identification works from the PDF alone | `apr FILE` on the 12-file corpus returns a title for every file |
| Online resolution is opt-in and accurate | `apr --online FILE` matches ground truth on ≥ 11 of 12 corpus files |
| Truncated/absent embedded metadata is not trusted | `2610.01939v2.pdf` offline yields the full title, not the 67-char `/Title` |
| Identifiers are extracted and normalised | DOIs and arXiv IDs from `/Info`, XMP, first page and file name |
| Machine-readable output contract | `docs/schema.md` matches actual output; `jq` consumes it |
| Honest uncertainty | `status`, per-field `provenance`, `warnings`; no invented values |
| No network unless asked | test suite fails if any test other than a marked `network` test opens a socket |
| Installable by a stranger | README's command works in a clean environment |

## Non-goals

Reference management, file renaming, PDF downloading, BibTeX/CSL output, OCR of
scanned pages, deduplication, a persistent library, GUI, PyPI publication,
non-PDF input, browser/extension integration.

## Deliverable

- Public repository `github.com/qjsoong/apr` at tag `v0.1.0`.
- README, LICENSE (MIT), CONTRIBUTING, output schema, non-goals.
- Source distribution and wheel attached to the GitHub release.
- CI green at the delivered commit: lint, type check, tests on Python
  3.10–3.13, package build + install smoke test.

## Constraints

- Python ≥ 3.10, one runtime dependency (`pypdf`), no compiled extensions, no
  API keys, no accounts, no cost.
- Offline by default. `--online` sends the extracted title, authors, year and
  identifiers to arXiv, Crossref, OpenAlex, Semantic Scholar and DataCite —
  never the PDF and never page text — and the README says so in the same breath
  as the flag.
- Zotero's `translate`/`zotero` sources are AGPL-3.0: read for design, **no code
  copied**.
- The 12 real PDFs in `~/doc/paper` are used for local evaluation only and are
  not redistributed in the repository.

## Acceptance checks (the bars)

Offline, over the 12-file corpus:

1. non-empty title for 12/12;
2. normalised title equal to ground truth for ≥ 9/12;
3. first author matching ground truth for ≥ 9/12;
4. `apr 2610.01939v2.pdf` returns the complete arXiv title;
5. zero sockets opened (enforced by the test-suite guard).

Online, over the same corpus:

6. normalised title equal to ground truth for ≥ 11/12;
7. first author matching ground truth for ≥ 11/12;
8. every network attempt appears in `sources` with a status.

Delivery:

9. `uv tool install git+https://github.com/qjsoong/apr` (or `pip install` from
   the wheel) in a clean environment, then `apr --version` and `apr FILE` work;
10. `uv run pytest`, `uv run ruff check .`, `uv run ty check` pass at the
    delivered commit;
11. CI ran green on that commit, and the release carries the wheel and sdist.

The numbers are floors, not targets: the scoreboard at the delivered commit is
recorded in `docs/evaluation.md`, including which files failed and why.

## Corpus facts found before implementation

- `2610.01939v2.pdf`, `2505.21469v4.pdf` — arXiv GenPDF: rich `/Info` and XMP,
  but the title and author list are **truncated**; first-page text is complete.
- `2306.12983v2.pdf`, `2310.16789v3.pdf` — older arXiv: `/Title` and `/Author`
  empty; first page carries title, authors and `arXiv:<id>`.
- Nature-family (`s41467-*`, `s43588-*`) — Springer/iText: `/doi`, `/Subject`
  with `doi:`, XMP with full author list, first page with the DOI URL; author
  lines in extracted text have broken spacing.
- Conference PDFs — descriptive file names (`NIPS-2017-…`, `ICLR-2025-…`),
  metadata sometimes empty, titles in caps with hyphenation artefacts.
- `2306.12983v2.pdf` and `Dubinski_…_WACV_2024_paper.pdf` are the same paper:
  one paper, two files, used as a pair fixture.

## Assumptions taken (and the cost of being wrong)

| Assumption | Cost if wrong |
| --- | --- |
| MIT licence | Replacing it is a one-file change before release |
| Python, one dependency, `pypdf` for text and layout | A different ecosystem means rewriting the extractor |
| Flat JSON, one object or NDJSON, `jq`-friendly | Schema change breaks downstream scripts written meanwhile |
| Exit 0 for `identified` *and* `probable` | Tightening to `identified` only changes scripts' control flow |
| Response cache under `XDG_CACHE_HOME/apr` | Callers wanting zero side effects use `--no-cache` |

## Standing authorization (given by the user)

- Any GitHub operation: create the public repository, push, tag, release, run
  Actions. No PyPI publication.
- Commit as progress is made, with conventional commit messages.
- Unattended iteration; independent review before acceptance.
