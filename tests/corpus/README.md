# Corpus ground truth (`tests/corpus`)

Evaluation corpus for apr against real, third-party PDFs, plus the answer key
(`expected.json`) that the metadata apr extracts is compared against.

## What the corpus is

The corpus is **12 real papers stored locally** in `~/doc/paper` (override the
location with `APR_CORPUS`). They are ordinary publisher and preprint PDFs:
four arXiv versions, five Nature-family articles, and three conference papers.

| File | Work |
| --- | --- |
| `2306.12983v2.pdf` | arXiv 2306.12983 v2 (WACV 2024 preprint) |
| `2310.16789v3.pdf` | arXiv 2310.16789 v3 (Min-K% Prob) |
| `2505.21469v4.pdf` | arXiv 2505.21469 v4 (PropMolFlow) |
| `2610.01939v2.pdf` | arXiv 2610.01939 v2 (PyRUA-Lean) |
| `Dubinski_..._WACV_2024_paper.pdf` | WACV 2024 published version of 2306.12983 |
| `ICLR-2025-min-k-...-Paper-Conference.pdf` | ICLR 2025 (arXiv 2404.02936) |
| `NIPS-2017-attention-is-all-you-need-Paper.pdf` | NeurIPS 2017 (arXiv 1706.03762) |
| `s41467-024-53632-4.pdf` | Nature Communications (TamGen) |
| `s41467-025-60763-9.pdf` | Nature Communications (G2D-Diff) |
| `s41467-026-75702-5.pdf` | Nature Communications (Mol-CADiff) |
| `s43588-024-00737-x.pdf` | Nature Computational Science (DiffSBDD) |
| `s43588-026-00986-y.pdf` | Nature Computational Science (SmileyLlama) |

## The PDFs are local and are not distributed

The PDFs are copyrighted by their authors and publishers. They are **not part
of this repository and must never be committed**: `.gitignore` excludes
`tests/corpus/*.pdf`, and nothing under `~/doc/paper` should ever be passed to
`git add`. Only `expected.json` and this file are versioned. Because the corpus
is local, tests that need it skip unless `APR_CORPUS` points at a directory of
PDFs (see the `corpus_dir` fixture in `tests/conftest.py`).

## What `expected.json` contains

`expected.json` is the **answer key**: for each PDF file name, the metadata that
an independent reader of the literature would agree the work has. apr's output
is scored against it, so it must not be derived from apr itself, and it was not
derived from the PDFs' own embedded metadata either.

| Field | Meaning |
| --- | --- |
| `title` | Title exactly as the chosen authority prints it, including its capitalisation |
| `authors` | Complete, ordered author list for the chosen record |
| `year` | Publication year of the version that is in the file |
| `venue` | Short, specific venue (`arXiv`, `Nature Communications`, `WACV 2024`, `ICLR 2025`, `NeurIPS 2017`) |
| `doi` | DOI of the chosen record, lower-cased, no `doi:` prefix, or `null` |
| `arxiv_id` | arXiv identifier of the chosen record, or `null` when the file is a non-arXiv version |
| `sources` | For every field, the URL of the record that establishes it |
| `title_alternates` | Optional. For the one file where two authorities print the same paper's title differently: a map of source URL to a wording that also counts as a match. Only the ICLR file has one, and its `notes` explain why |
| `notes` | Version details, ambiguities, confirmations and known disagreements |

`verified_at` is the UTC date on which the whole file was last checked against
the authorities listed in `sources`.

### How each field was verified

- **arXiv files** (`2306.12983v2`, `2310.16789v3`, `2505.21469v4`,
  `2610.01939v2`): the arXiv API
  (`export.arxiv.org/api/query?id_list=<id>`) and the arXiv abstract pages.
  Authors and titles come from the API entry; the version dates used for `year`
  come from the "Submission history" block of the abstract page, so `year` is
  the year of the version in the file, not of v1.
- **Nature-family files**: Crossref (`api.crossref.org/works/<doi>`) for the
  DOI, publication date, journal and author list, cross-checked against the
  publisher landing page (`nature.com/articles/<doi-suffix>`), which prints the
  same title and the same ordered author list.
- **Conference files**: the published record for the version in the file — the
  IEEE/Crossref record for WACV 2024, the ICLR proceedings record for ICLR
  2025, the NeurIPS proceedings record for NeurIPS 2017. The identifying
  record, not the file name alone, decided the title, authors, year and venue.

Two limitations are worth knowing. First, the file→work link rests on the
identifier in the file name, which was verified to resolve to a record whose
subject matches; the PDFs' internal metadata and text were not parsed during
verification. Second, where two authorities disagree (name diacritics, a
`Detecting`/`Detection` wording difference, pagination), the entry records the
authority that matches the version in the file and lists the disagreement, with
both URLs, in `notes`.

### One paper, two files, two legitimate answers

`2306.12983v2.pdf` and the Dubinski WACV 2024 file are the **same paper**:
one preprint and one published version. That is deliberate, and the expected
values differ:

- `2306.12983v2.pdf` → `venue` `arXiv`, `year` 2023, `doi` `null`
- WACV file → `venue` `WACV 2024`, `year` 2024,
  `doi` `10.1109/wacv57701.2024.00479`

Neither answer is wrong, and a tool that normalises one into the other loses
information. The same principle applies more generally: a work's year and venue
depend on which version a file contains, so `year` and `venue` in this file
describe **that file's version** and nothing else. `arxiv_id` is left `null`
for a file that is not the arXiv version even when the work also exists on
arXiv; the arXiv identifier is then recorded in `notes`.

## Re-verifying the file

Re-verification needs the network; the test suite is offline by default, so do
it manually and only use `APR_TEST_ALLOW_NETWORK=1` with `@pytest.mark.network`
if you add a test for it.

1. Check that the JSON still parses and every field has a URL:

   ```sh
   python3 -m json.tool tests/corpus/expected.json > /dev/null
   python3 - <<'PY'
   import json, pathlib
   data = json.loads(pathlib.Path("tests/corpus/expected.json").read_text())
   for name, entry in data["files"].items():
       for field, value in entry.items():
           if field in {"sources", "notes"}:
               continue
           source = entry["sources"].get(field)
           assert (value is None) == (source is None), (name, field)
           assert source is None or source.startswith("http"), (name, field)
   PY
   ```

2. Re-fetch each `sources` URL and compare it with the matching field:

   ```sh
   curl -s "https://export.arxiv.org/api/query?id_list=2306.12983"   # title, authors
   curl -s "https://arxiv.org/abs/2310.16789"                        # version dates
   curl -s "https://api.crossref.org/works/10.1038/s41467-024-53632-4"
   curl -s "https://api.crossref.org/works/10.1109/wacv57701.2024.00479"
   curl -s "https://proceedings.neurips.cc/paper_files/paper/2017/hash/\
   3f5ee243547dee91fbd053c1c4a845aa-Abstract.html"
   ```

3. For a version-bearing record, confirm the file really is the version its
   name claims: compare the arXiv submission history, or the publication date
   in Crossref, with the version named in the PDF file name.

4. Update `verified_at`, and record in `notes` anything that changed — a new
   arXiv version, a corrected author list, or a new disagreement.

`expected.json` keeps its keys sorted, one entry per PDF file name, so a diff
of a re-verification is readable.
