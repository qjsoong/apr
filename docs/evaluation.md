# Evaluation

What `apr` scores on twelve real papers, how that was measured, and what the
numbers do not prove. Measured at revision `47606ce` (2026-10-15, UTC) on Linux
(Python 3.13, `pypdf` as installed by `uv sync`). That revision is the last one
that changes anything under `src/`, `scripts/` or `tests/` before the release;
the commits after it are documentation, and
`git diff --stat 47606ce..HEAD -- src scripts tests` is empty.

```console
export APR_CORPUS=~/doc/paper
uv run python scripts/eval_corpus.py             # offline
uv run python scripts/eval_corpus.py --online    # online, with the response cache
```

## The corpus

Twelve papers chosen for their variety of failure modes rather than their
convenience:

| Kind | Files | What it exercises |
| --- | --- | --- |
| arXiv, rich metadata | 2 | `/Title` truncated at 67 characters, `/Author` cut mid-name; first-page text is the real source |
| arXiv, empty metadata | 2 | no `/Title` or `/Author` at all; title, authors and `arXiv:` stamp only on page 1 |
| Publisher (Nature family) | 5 | `/Info` lists one author where XMP lists all; DOI in `/Subject`, `/doi` and the page-1 URL; author lines broken by font subsetting |
| Conference proceedings | 3 | descriptive file names, caps-with-hyphenation titles, `Published as a conference paper at ICLR 2025` headers, no identifiers |
| One paper, two files | 1 pair | `2306.12983v2.pdf` and the WACV 2024 published version are the same work with different correct years, venues and DOIs |

The expectation for every file is in [../tests/corpus/expected.json](../tests/corpus/expected.json);
each field carries the URL of the authority that establishes it (arXiv API,
Crossref, ICLR/NeurIPS proceedings pages). The file was written by a separate
agent that never looked at the implementation, and the parent then re-derived
every title, first author and author count from those authorities before
integrating it — an answer key that has not been checked is not an answer key.
The PDFs are copyrighted and are not in the repository.

Comparison is deliberately independent of `apr`'s own matching code: titles are
casefolded, accent-folded and punctuation-flattened, then compared for
equality. One title is allowed a documented alternate, because the ICLR record
prints *Pre-Training Data Detection from* where arXiv prints *Detecting
Pre-Training Data from* — same paper, same eight authors.

## Results

Bars are the floors agreed before implementation in
[../notes/CONTRACT.md](../notes/CONTRACT.md). A result column of `-` means the
file carries no such identifier, so there is nothing to compare.

| Measure | Offline (`apr FILE`) | Online (`apr --online FILE`) | Floor (offline / online) |
| --- | --- | --- | --- |
| Non-empty title | 12/12 | 12/12 | 12/12 / — |
| Title exactly equal to the authority | **12/12** | **12/12** | 9/12 / 11/12 |
| …of which matched a documented alternate wording | 1 | 1 | — |
| First author (surname) equal | **12/12** | **12/12** | 9/12 / 11/12 |
| Year equal | 12/12 | 12/12 | not a bar |
| DOI equal, where the file has one | 4/5 | 5/5 + WACV | not a bar |
| arXiv ID equal, where applicable | 4/4 | 4/4 | not a bar |
| Mean title similarity | 0.99 | 0.99 | — |
| `status` | 12 `identified` | 12 `identified` | — |
| `confidence` range | 0.71–0.80 | 0.94–1.00 | — |
| Time per file | 0.10–0.15 s | 0.10–2.75 s | — |
| Network attempts recorded in `sources` | 0 | 19 | — |

The one imperfect offline cell is the WACV paper's DOI: the PDF carries no DOI,
so offline `apr` cannot know the published version's `10.1109/wacv57701.2024.00479`.
Online it finds it by title. Nothing else on any file was wrong in either mode.

Two of these cells are weaker evidence than the rest, and the reader should know
which. The title column includes one file whose title matches a *different
authority's wording* of the same paper (the ICLR record prints `Pre-Training Data
Detection from` where arXiv prints `Detecting Pre-Training Data from`); excluding
that allowance the honest figure is 11/12 plus one correct identification. And
the year column for the two conference files agrees partly because apr's year
hint and the answer key's year both come from the file name (the venues are not
in question: those PDFs print `ICLR 2025` and `NIPS 2017` on page 1). Titles and
first authors do not have that weakness: renamed to neutral names, the corpus
still scores 12/12 and 12/12, with no title attributed to the file-name layer
(see the structural checks below).

Two cases are worth naming because they are the ones a plausible implementation
gets wrong:

- `2610.01939v2.pdf` embeds a `/Title` truncated to `…with 14`. Both modes
  return the complete printed title, and the offline record says why:
  `embedded title looks truncated; used the title printed on page 1`.
- `NIPS-2017-attention-is-all-you-need-Paper.pdf` has a perfect-title trap:
  Crossref contains several `posted-content` records of that famous paper,
  registered by an unrelated academy, dated 2025. `apr --online` keeps the
  document's own 2017, returns no DOI, and records five warnings including
  `local year 2017 disagrees with the 2025 record from crossref; keeping the
  local year`.

## Structural checks

These are not accuracy measurements; each one is a claim the README makes.

| Claim | How it was checked | Result |
| --- | --- | --- |
| Offline mode opens no sockets | The whole offline corpus run executes inside `unshare -rn` (empty network namespace, no DNS) | 12/12, `sources: []` |
| The same, at syscall level | `apr FILE` under a Python audit hook that aborts on any `socket.*` event | no events, exit 0 |
| `--online` sends only extracted fields | Read of the request construction: title, first author, identifiers, optional `mailto`; never page text or the file | verified by inspection |
| Output is deterministic | Byte comparison of a live run and the same run served from cache | identical |
| Cache writes nowhere else | `XDG_CACHE_HOME/apr`, one `{url, body}` file per request; `--no-cache` writes nothing; the checkout stays clean | verified |
| The documented install works | `uv run --isolated --no-project --with git+https://github.com/qjsoong/apr apr --version` in a throwaway environment, no credentials | `apr 0.1.0` |
| Artifacts carry the licence | `unzip -l dist/apr-0.1.0-py3-none-any.whl` and the sdist | `LICENSE` in both; wheel contains only the package |
| The point of the tool works | A shell loop renames three corpus PDFs from `jq`-extracted fields, offline | `Vaswani 2017 - Attention Is All You Need.pdf`, etc. |
| Titles and first authors do not depend on the file name | Copied the corpus to neutral names (`01.pdf`…`12.pdf`) so the file-name layer had nothing to work with, and re-measured | 12/12 titles, 12/12 first authors, and no title's provenance was the file name |

## Defects the measurements found and fixed

The first measurement was not a pass. Each of these was found by running the
corpus or by an independent review, not by reading the code, and each is now
pinned by a test:

| Defect | Found by |
| --- | --- |
| Fixture PDFs checked out as CRLF on Windows, corrupting every xref offset | CI on the Windows jobs (`tests/fixtures/*.pdf -text` was missing) |
| OpenAlex author names returned as `Zeng, Cheng` for some authors and `Jirui Jin` for others in one record | first-author column, `2505.21469v4.pdf` |
| A preprint record replaced a venue and year the PDF itself printed (`ICLR 2025` → `arXiv`, 2025 → 2024) | venue/year columns, ICLR file |
| Crossref search reported `not-found` for a record that exists and is Crossref's own top hit | DOI column, WACV file |
| A 2025 `posted-content` record of a 2017 paper won on a perfect title match and overrode the year | year column, `NIPS-2017…` file |
| arXiv year taken from the first version rather than the version in the file (`v3`, 2024) | year column, `2310.16789v3.pdf` |
| `path` reported the stdin spool file (`/tmp/apr-stdin-….pdf`) instead of `-` | comparing output against `docs/schema.md` |
| `--cache-dir` documented `$XDG_CACHE_HOME/apr` while the default was no cache at all | looking for the cache after an online run |
| `docs/schema.md` disagreed with real output in four places: exit 141 undocumented, the `online` provenance source missing, `skipped` described as offline, and invented method and warning strings | independent review, against the contract |
| A spent request budget was visible only on stderr with `-v`, so a caller could not tell "provider had nothing" from "apr stopped asking" | independent review |
| A disagreement between an identifier's record and the printed text was reported only in human-readable `warnings` | independent review |
| The cache stored the extracted title and any `--mailto` address world-readable | independent review |

## Limits

- **Twelve files is small**, they are all English-language research papers, and
  they were not selected at random. The floors were set from this corpus; treat
  the full marks as "these twelve work", not as a general accuracy estimate.
- **The corpus scoreboard runs on Linux only.** The synthetic tests run on
  Linux, macOS and Windows in CI; the copyrighted corpus cannot.
- **Online results are a snapshot.** A third-party record can be corrected,
  moved or spam-created after 2026-10-15; the numbers are not reproducible
  offline, though the recorded fixtures under `tests/recorded/` reproduce the
  parsing and scoring behaviour.
- **Answers were judgement calls in places**: which version of a paper to treat
  as the answer, and whether `year` means the version in the file or the first
  version. `tests/corpus/README.md` records the rules and the disagreements.
- **Not covered**: scanned PDFs with no text layer (no OCR), non-English papers,
  PDFs above ~100 MB, encrypted PDFs beyond reporting them as unreadable, and
  anything about metadata *after* the paper is identified.
