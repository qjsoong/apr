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

> **Status:** v0.1.0 under construction, not yet released. Install from Git
> (below) until a tagged release exists.

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
apr paper.pdf                    # offline: the PDF alone
apr --online paper.pdf           # also ask arXiv / Crossref / OpenAlex / Semantic Scholar
apr --pretty --online paper.pdf  # human-readable
apr a.pdf b.pdf                  # NDJSON, one object per file
curl -sL https://arxiv.org/pdf/2610.01939v2 | apr -      # one PDF on stdin
```

Offline is the default and opens no sockets. `--online` sends the identifiers
and first-page text that `apr` found to the services listed below; nothing is
sent anywhere else, and nothing is stored anywhere but your cache directory
(`--no-cache`, `--cache-dir`). Per-field provenance in the output shows who
answered what.

Typical offline output (abridged):

```json
{
  "schema_version": 1,
  "apr_version": "0.1.0",
  "path": "2610.01939v2.pdf",
  "status": "identified",
  "confidence": 0.92,
  "title": "Fewer Tokens, Better Action: GPT-6 Astra Robot Agents with 14% Higher Success Rate but 65% Fewer Tokens",
  "authors": ["Ruiyang Si", "Jianxin Bi"],
  "year": 2026,
  "venue": null,
  "doi": null,
  "arxiv_id": "2610.01939",
  "arxiv_version": "v2",
  "provenance": {
    "title": {"source": "pdf-first-page", "method": "layout:largest-font", "confidence": 0.85}
  },
  "sources": []
}
```

Every field, every enum and the exit codes are specified in
[docs/schema.md](docs/schema.md).

## What it reads

| Where | What | Notes |
| --- | --- | --- |
| File name | arXiv IDs, DOIs, journal-style identifiers, leading years | `2610.01939v2.pdf`, `s41467-024-53632-4.pdf` |
| PDF `/Info` and XMP | Title, authors, DOI, journal, creation year | often truncated, sometimes empty |
| First page text | DOI / arXiv ID, title, abstract | the most reliable offline source |
| First page layout | The title in the largest font above the abstract, author line under it | survives caps, hyphenation and line breaks |
| Online services | Authoritative records and bibliographic search | only with `--online` |

PDF metadata lies more often than people expect: arXiv's own generator truncates
`/Title` at 67 characters, many LaTeX PDFs write an empty `/Author`, and
publisher XMP sometimes lists only the first author. `apr` prefers first-page
text over embedded metadata when the two disagree, and says so in `warnings` and
`provenance`.

## Non-goals

Not a reference manager. Not a renamer. No BibTeX/CSL formatting, no download,
no fetching by DOI/URL, no OCR for scanned PDFs, no deduplication, no library,
no GUI. If you need those, Zotero, `paper-rename`-style scripts and `curl`
already exist; `apr` is the part they can call.

## Data sources (only with `--online`)

[arXiv API](http://export.arxiv.org/api/query), [Crossref](https://api.crossref.org),
[OpenAlex](https://api.openalex.org), [Semantic Scholar Graph API](https://api.semanticscholar.org),
[DataCite](https://api.datacite.org) for DOI resolution fallbacks. All keyless.
Set `--mailto you@example.com` (or `APR_MAILTO`) to identify yourself to the
polite pools, as those services ask.

The identification strategy — extract locally, identify by identifier, fall back
to bibliographic search, then score candidates by title and author similarity —
follows how Zotero's *Retrieve Metadata for PDF* works. Zotero's code is
AGPL-3.0 and none of it is used here; this is an independent implementation.

## Development

```console
git clone https://github.com/qjsoong/apr && cd apr
uv sync                      # dev environment
uv run pytest                # tests (network-free)
uv run ruff check . && uv run ruff format --check .
uv run ty check
```

Evaluation against a corpus of real PDFs (not distributed with the repository):

```console
export APR_CORPUS=~/doc/paper
uv run python scripts/eval_corpus.py            # offline scoreboard
uv run python scripts/eval_corpus.py --online
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks CI runs, and
[notes/CONTRACT.md](notes/CONTRACT.md) for the scope this release was built to.

## Licence

MIT — see [LICENSE](LICENSE).
