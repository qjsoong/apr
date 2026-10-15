# apr — academic paper recognizer

`apr` reads a PDF and tells you **which paper it is**: title, authors, year,
venue, DOI, arXiv ID. Output is JSON on stdout, so the next program in a
pipeline can rename the file, build a citation or deduplicate a folder.

```console
$ apr 2610.01939v2.pdf | jq -r '.title'
Fewer Tokens, Better Action: GPT-6 Astra Robot Agents with 14% Higher Success Rate but 65% Fewer Tokens
```

It does one thing. It does not manage a library, rename your files, download
anything or keep a database.

> **Status:** v0.1.0. Install from Git with the command below; there is no PyPI
> package.

## Install

```console
# run without installing
uvx --from git+https://github.com/qjsoong/apr apr --help

# install the command
uv tool install git+https://github.com/qjsoong/apr
# or
pip install "git+https://github.com/qjsoong/apr"
```

Requires Python 3.10 or newer. One runtime dependency (`pypdf`); no compiler, no
API keys, no accounts.

## Use

```console
apr paper.pdf                    # offline: the PDF alone (the default)
apr --online paper.pdf           # also ask scholarly APIs
apr --pretty --online paper.pdf  # human-readable
apr a.pdf b.pdf                  # NDJSON, one object per file
curl -sL https://arxiv.org/pdf/2610.01939v2 | apr -      # one PDF on stdin
```

Output below is real: `apr --pretty --no-abstract s41467-026-75702-5.pdf`, with only
the `sha256` line removed. Note what the record admits about itself: the DOI
comes from `/Info`, the author list from XMP because `/Info` lists one author,
and the disagreement is reported in `warnings`.

```json
{
  "schema_version": 1,
  "apr_version": "0.1.0",
  "path": "s41467-026-75702-5.pdf",
  "status": "identified",
  "confidence": 0.8,
  "bytes": 2814073,
  "pages": 15,
  "title": "Mol-CADiff: text-conditional molecule generation via causality-aware autoregressive diffusion",
  "authors": ["Md Atik Ahamed", "Qiang Ye", "Qiang Cheng"],
  "year": 2026,
  "venue": "Nature Communications",
  "doi": "10.1038/s41467-026-75702-5",
  "arxiv_id": null,
  "arxiv_version": null,
  "url": null,
  "abstract": null,
  "warnings": ["/Info lists 1 author(s) and XMP lists 3; XMP is preferred"],
  "provenance": {
    "title": {"source": "pdf-first-page", "method": "layout:largest-font", "confidence": 0.85},
    "authors": {"source": "pdf-xmp", "method": "xmp:dc:creator", "confidence": 0.8},
    "year": {"source": "pdf-first-page", "method": "text:journal-line", "confidence": 0.6},
    "venue": {"source": "pdf-first-page", "method": "text:journal-line", "confidence": 0.7},
    "doi": {"source": "pdf-info", "method": "info:doi", "confidence": 0.7}
  },
  "sources": []
}
```

Every field, every enum and the exit codes are specified in
[docs/schema.md](docs/schema.md).

Options that matter: `--online` (opt-in network use), `--why` (show every value
considered and whether it won), `--no-abstract`, `--pretty`, `--mailto`,
`--timeout`, `--no-cache`, `-v`.

## Recipe: rename a folder by title and author

The point of the JSON is that something else consumes it. Offline, on a folder of
messy file names:

```sh
for f in *.pdf; do
  meta=$(apr --no-abstract "$f") || continue
  title=$(printf '%s' "$meta" | jq -r '.title // empty' | tr -d '/:*?"<>|' | cut -c1-90)
  author=$(printf '%s' "$meta" | jq -r '.authors[0] // empty' | awk '{print $NF}')
  year=$(printf '%s' "$meta" | jq -r '.year // empty')
  [ -n "$title" ] && mv -n -- "$f" "$author $year - $title.pdf"
done
```

```text
2610.01939v2.pdf                          -> Si 2026 - Fewer Tokens, Better Action ....pdf
NIPS-2017-attention-is-all-you-need-Paper.pdf -> Vaswani 2017 - Attention Is All You Need.pdf
s41467-026-75702-5.pdf                    -> Ahamed 2026 - Mol-CADiff text-conditional ....pdf
```

Check `status` and `provenance` if you would rather not rename on a guess, and
truncate as crudely as your filesystem demands: apr identifies papers, it does
not decide what a good file name is.

## Offline by default

`apr paper.pdf` opens no sockets. That is not a promise about intent, it is
checked: the test suite replaces `socket.connect` with a function that fails,
and the corpus evaluation has been run inside an empty network namespace
(`unshare -rn`) where nothing resolves.

With `--online`, apr sends **the title, authors, year and identifiers it
extracted — not the PDF, and not the page text** — to these keyless services:

| Service | Used for |
| --- | --- |
| [arXiv API](http://export.arxiv.org/api/query) | identifier lookup and title/author search |
| [Crossref](https://api.crossref.org) | DOI lookup and bibliographic search |
| [OpenAlex](https://api.openalex.org) | DOI lookup and search |
| [Semantic Scholar](https://api.semanticscholar.org) | DOI lookup and title matching |
| [DataCite](https://api.datacite.org) | DOI fallback |

Set `--mailto you@example.com` (or `APR_MAILTO`) to identify yourself to the
polite pools, as those services ask. Responses are cached under
`$XDG_CACHE_HOME/apr`; `--no-cache` disables that. Nothing is sent anywhere
else, and apr operates no service of its own.

## How it decides

1. **Read the file first.** File name, `/Info`, XMP, and the first page: text,
   and where the text sits on the page.
2. **Prefer what the document shows over what it claims.** PDF metadata lies in
   predictable ways — arXiv's generator truncates `/Title` at 67 characters,
   many LaTeX PDFs write an empty `/Author`, Springer XMP lists only the first
   author. A title set in the largest font above the abstract beats a truncated
   `/Title` field, and the disagreement is reported in `warnings`.
3. **Identify, then search.** With `--online`, a DOI or arXiv ID resolves the
   record directly. Otherwise the locally extracted title and authors drive a
   bibliographic search, and candidates are scored on title, author and year
   similarity before anything is accepted.
4. **Say who said what.** Every accepted value carries `source`, `method` and
   `confidence`; `--why` shows the values that lost. A record that cannot be
   checked is marked `probable` rather than `identified`.

## What it reads

| Where | What | Notes |
| --- | --- | --- |
| First-page layout | Title, authors, abstract | Font-size aware: the title is the largest run above the abstract, wrapped lines and letterspacing artefacts repaired |
| First-page text | DOI, arXiv ID, conference/journal lines, year hints | `arXiv:2310.16789v3`, `Published as a conference paper at ICLR 2025` |
| PDF `/Info` and XMP | Title, authors, DOI, journal | Trusted less than the page when they disagree, truncation detected, and the disagreement reported |
| File name | arXiv IDs, DOIs, year and venue hints, and a weak title guess from name words | The weakest layer: everything from it is offered at 0.5 confidence or less, so page text and metadata always win; never authoritative |
| Online services | Authoritative records, bibliographic candidates | Only with `--online` |

## How this differs from Zotero

Zotero's *Retrieve Metadata for PDF* is the closest well-known feature, and
[docs/design/pdf-recognition.md](docs/design/pdf-recognition.md) documents how it
works, with pinned revisions and evidence labels. Neither project's code is in
the other: Zotero is AGPL-3.0, apr is MIT, and only the architecture was
studied. The differences are deliberate:

| | Zotero | apr |
| --- | --- | --- |
| Network | Always: five pages of word-level text and the file name go to `services.zotero.org` | Off by default; only extracted fields go to public APIs, and only with `--online` |
| Matcher | Server-side, closed; the client reads no score and sets no threshold | In-tree, scored on title/author/year similarity, thresholds visible and tested |
| Verification | Accepts a returned DOI/arXiv ID without checking it against the PDF | Cross-checks an identifier lookup against the printed text and warns when they disagree, e.g. a stale DOI |
| Output | A library item | JSON on stdout, plus per-field provenance |
| Scope | Library management, renaming, sync | Identify one paper and stop |

## Non-goals

Not a reference manager. Not a renamer. No BibTeX/CSL formatting, no download,
no fetching by DOI/URL, no OCR for scanned PDFs, no password cracking for
encrypted PDFs (they are reported as an error), no deduplication, no library, no
GUI, no PyPI publication. If you need those, Zotero and `curl` already exist;
`apr` is the part they can call.

## Limits worth knowing

- **Scanned PDFs**: no text layer means no title; apr says so instead of
  guessing. There is no OCR.
- **Offline abstracts** are extracted only when page 1 prints an `Abstract`
  heading — Nature-family PDFs do not, so `--online` is what fills those in.
- **Capitalisation is preserved as printed.** An ICLR-style title set in caps
  comes back in caps offline; `--online` returns the properly cased title.
- **`year` means the year of the version in the file**, which for a paper with
  several preprint versions is the version's own date, not the first one's.
- **Preprint vs published**: with `--online`, a preprint record never overrules
  a venue or year that the document itself prints; a published record is
  preferred when both match.

## Evaluation

Twelve real papers — arXiv, IEEE, ICLR, NeurIPS and Nature-family PDFs, each
with different metadata quirks — are scored against
[tests/corpus/expected.json](tests/corpus/expected.json), whose every field was
verified against arXiv, Crossref, DBLP and the publishers' own pages by someone
who did not write the code. The numbers, the failures and the method are in
[docs/evaluation.md](docs/evaluation.md). The corpus is copyrighted and is not
distributed with the repository.

## Development

```console
git clone https://github.com/qjsoong/apr && cd apr
uv sync                      # dev environment
uv run pytest                # tests, network-free
uv run ruff check . && uv run ruff format --check .
uv run ty check
```

With a local corpus of your own PDFs (`APR_CORPUS=~/doc/paper`):

```console
uv run pytest                                    # now includes the corpus tests
uv run python scripts/eval_corpus.py             # offline scoreboard
uv run python scripts/eval_corpus.py --online     # online scoreboard
APR_TEST_ALLOW_NETWORK=1 uv run python scripts/live_smoke.py   # live lookups, no corpus
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks CI runs and the rules the
tests enforce, and [notes/CONTRACT.md](notes/CONTRACT.md) for the scope this
release was built to.

## Licence

MIT — see [LICENSE](LICENSE).
