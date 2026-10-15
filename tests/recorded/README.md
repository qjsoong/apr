# Recorded API responses

Small excerpts of real responses from the keyless scholarly APIs, captured on
**2026-10-16** with `User-Agent: apr/0.1.0 (+https://github.com/qjsoong/apr)`.
They let the provider tests run without a socket: the test suite replaces the
HTTP layer with these bodies (see `tests/test_providers_http.py` and
`tests/test_providers_resolve.py`).

Every value is verbatim. Trimming removed fields the parsers never read, and
long abstracts were cut at a word boundary with a trailing `...`. Nothing was
edited, reordered or prettified by hand.

| File | Endpoint and query | Trimmed to |
| --- | --- | --- |
| `arxiv_id_2306.12983.xml` | `export.arxiv.org/api/query?id_list=2306.12983` | the single entry (no DOI, no journal ref) |
| `arxiv_id_2105.04565.xml` | `export.arxiv.org/api/query?id_list=2105.04565` | the fields the parser reads; this entry carries `arxiv:doi` and `arxiv:journal_ref` |
| `arxiv_search_attention.xml` | `export.arxiv.org/api/query?search_query=ti:"Attention is all you need" AND au:"Vaswani"&max_results=5` | the single entry |
| `crossref_doi_10.1038_s41467-024-53632-4.json` | `api.crossref.org/works/10.1038/s41467-024-53632-4` | identifiers, title, journal, authors, dates and the first JATS abstract paragraph |
| `crossref_doi_10.1103_physrevd.104.095029.json` | `api.crossref.org/works/10.1103/PhysRevD.104.095029` | identifiers, title, journal, authors, dates |
| `crossref_search_membership_inference.json` | `api.crossref.org/works?query.bibliographic=Towards More Realistic Membership Inference Attacks on Large Diffusion Models&query.author=Dubiński&rows=5` | the five items, without reference lists; item 1 is the paper, items 2–5 are near misses |
| `openalex_doi_10.1038_s41467-024-53632-4.json` | `api.openalex.org/works/doi:10.1038/s41467-024-53632-4` | identifiers, title, year, type, primary location, first three authorships, the first 14 positions of the inverted abstract |
| `openalex_search_attention.json` | `api.openalex.org/works?search=Attention is all you need&per-page=5` | the five results, without concepts, topics and counts |
| `semanticscholar_match_attention.json` | `api.semanticscholar.org/graph/v1/paper/search/match?query=Attention is all you need&fields=…` | the matched paper: title, year, venue, external ids, four authors, abstract prefix |
| `semanticscholar_doi_10.1109_wacv57701.2024.00479.json` | `api.semanticscholar.org/graph/v1/paper/DOI:10.1109/WACV57701.2024.00479?fields=…` | title, year, venue, external ids, four authors, abstract prefix |
| `datacite_doi_10.5281_zenodo.18289746.json` | `api.datacite.org/dois/10.5281/zenodo.18289746` | identifiers, title, year, creators, abstract prefix |
| `malformed_body.json` | — | **not** a real response: a body truncated mid-JSON, to test the malformed-body path |

Refresh one file with:

```console
curl -sS -H 'User-Agent: apr/0.1.0 (+https://github.com/qjsoong/apr)' \
  'https://export.arxiv.org/api/query?id_list=2306.12983'
```

Two services rate-limit keyless clients: Semantic Scholar answers `429` under
load, and arXiv asks for about one request every three seconds. The live smoke
script (`scripts/live_smoke.py`) reports what each endpoint answered.
