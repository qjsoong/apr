# Output schema (v1)

`apr` writes one JSON object per input file to stdout. Nothing else — no logs on
stdout, no colour, no progress. Field names are stable within
`schema_version: 1`; new optional fields may be added in a minor release.

## Single file

```json
{
  "schema_version": 1,
  "apr_version": "0.1.0",
  "path": "2610.01939v2.pdf",
  "status": "identified",
  "confidence": 0.93,
  "bytes": 3330964,
  "sha256": "…",
  "pages": 34,
  "title": "Fewer Tokens, Better Action: …",
  "authors": ["Ruiyang Si", "Jianxin Bi"],
  "year": 2026,
  "venue": "arXiv",
  "doi": null,
  "arxiv_id": "2610.01939",
  "arxiv_version": "v2",
  "url": "https://arxiv.org/abs/2610.01939v2",
  "abstract": "…",
  "warnings": ["embedded title looks truncated; used the title printed on page 1"],
  "provenance": {
    "title": {"source": "pdf-first-page", "method": "layout:largest-font", "confidence": 0.8},
    "authors": {"source": "pdf-first-page", "method": "layout:author-block", "confidence": 0.7}
  },
  "sources": [{"source": "arxiv", "status": "ok", "query": "id_list=2610.01939", "candidates": 1}]
}
```

## Several files

One object per line (NDJSON), in input order. With `--pretty`, a JSON array
instead, so that a pretty-printed stream stays valid JSON.

## Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | int | Output schema version. |
| `apr_version` | string | Version of apr that produced the record. |
| `path` | string | Input path as given (`-` for stdin, which is spooled) . |
| `status` | enum | `identified`, `probable`, `unidentified`. |
| `confidence` | number | Weighted mean of the confidences of the fields present. |
| `bytes`, `sha256`, `pages` | int, string, int | Facts about the file; absent if unknown. |
| `title` | string \| null | Paper title. |
| `authors` | array | Author names, in order, as printed by the source. |
| `year` | int \| null | Publication year, not the PDF creation date. |
| `venue` | string \| null | Journal, conference or preprint server. |
| `doi` | string \| null | Lowercased DOI without the `doi:` prefix. |
| `arxiv_id`, `arxiv_version` | string \| null | `2610.01939` and `v2`. |
| `url` | string \| null | Landing page for the canonical record. |
| `abstract` | string \| null | `null` with `--no-abstract` (the key stays, so consumers see a stable shape). |
| `warnings` | array | Present only when something was noticed, e.g. truncated metadata. |
| `disputed` | `true` | Present only when a looked-up record contradicts the printed text. |
| `provenance` | object | Per field: `source`, `method`, `confidence` of the accepted value. |
| `sources` | array | One entry per online lookup: `source`, `status`, `query`/`url`, `candidates`, `error`. Empty offline. |
| `evidence` | array | Only with `--why`: every value considered. `applied` is true when the value was accepted at the moment it was offered; a later, better-evidenced value can still replace it, so `provenance` is what says who won. |

## Values and meaning

- `doi`, `arxiv_id` and `arxiv_version` describe the **paper**, not necessarily
  the file. Read from the file, `method` starts with `info:`, `xmp:`, `text:` or
  `name:`; filled by an online match, it names the matched record instead
  (`arxiv: id_list=…`, `crossref: query.bibliographic=…`). So a proceedings PDF
  can carry the arXiv identifier of the same paper, and a preprint PDF can carry
  the published DOI. `provenance` is what tells the two apart, and a caller that
  needs the file's own identifier should check the method or read the local
  method only (no network: `apr FILE`).

- `confidence` is a weighted mean over `title` (0.45), `authors` (0.35), `year`
  (0.10), `doi` (0.05), `arxiv_id` (0.05), renormalised over the fields that are
  actually present. A record with a perfect title and nothing else scores 1.0,
  which is why `status` — not `confidence` alone — carries the verdict.
- `status` is `unidentified` without a title, `identified` when the title is
  confident (≥ 0.70) and at least one author is known, `probable` otherwise.
- `source` names the stage: `filename`, `pdf-info`, `pdf-xmp`, `pdf-first-page`,
  `arxiv`, `crossref`, `openalex`, `semanticscholar`, `datacite`, or `online` for
  the single entry recorded when `--online` had nothing to look up.
- `sources[].status` is `ok`, `not-found`, `error` or `skipped`. Offline,
  `sources` is always empty. Online, `skipped` means the request was not made —
  in practice because the per-file request budget was already spent, which the
  entry's `error` says.
- `disputed` appears only when a value contradicts something the document itself
  prints: an identifier lookup whose record disagrees with the title on the page.
  The identifier's values still win — that is what the identifier says — but a
  caller can refuse to trust them without parsing `warnings`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every input was identified or probable. |
| 1 | At least one input produced no title. |
| 2 | At least one input could not be read (and `apr:` message on stderr). |
| 130 | Interrupted (SIGINT). |
| 141 | stdout was a closed pipe (`apr f.pdf \| head -1`). |

`2` outranks `1`: a file that could not be read is an error, not a failed
identification.
