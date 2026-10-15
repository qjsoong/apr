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
| `arxiv_id_2310.16789.xml` | `export.arxiv.org/api/query?id_list=2310.16789&max_results=1` | the v3 entry: `<published>` dates v1 (2023-10-25) and `<updated>` v3 (2024-03-09) |
| `arxiv_search_attention.xml` | `export.arxiv.org/api/query?search_query=ti:"Attention is all you need" AND au:"Vaswani"&max_results=5` | the single entry |
| `arxiv_search_membership_inference.xml` | `export.arxiv.org/api/query?search_query=ti:"Towards More Realistic Membership Inference Attacks on Large Diffusion Models" AND au:"Dubinski"&max_results=5` | the v2 preprint entry (published 2023) of the WACV paper |
| `arxiv_search_min_k_prime.xml` | `export.arxiv.org/api/query?search_query=ti:"MIN-K%++: IMPROVED …" AND au:"Zhang"&max_results=5` | the v4 preprint entry (published 2024) of the ICLR 2025 paper |
| `crossref_doi_10.1038_s41467-024-53632-4.json` | `api.crossref.org/works/10.1038/s41467-024-53632-4` | identifiers, title, journal, authors, dates and the first JATS abstract paragraph |
| `crossref_doi_10.1103_physrevd.104.095029.json` | `api.crossref.org/works/10.1103/PhysRevD.104.095029` | identifiers, title, journal, authors, dates |
| `crossref_doi_10.1038_s41467-025-60763-9.json` | `api.crossref.org/works/10.1038/s41467-025-60763-9` | identifiers, title, journal, authors, dates; used to model a PDF that carries another paper's DOI |
| `crossref_search_membership_inference.json` | `api.crossref.org/works?query.bibliographic=Towards More Realistic Membership Inference Attacks on Large Diffusion Models&query.author=Dubiński&rows=5` | the five items, without reference lists; item 1 is the paper, items 2–5 are near misses |
| `crossref_search_membership_inference_plain.json` | `api.crossref.org/works?query.bibliographic=Towards More Realistic Membership Inference Attacks on Large Diffusion Models&rows=5` | the same five items for the plain-title query; item 1 is the paper |
| `crossref_search_min_k_prime_plain.json`, `…_author.json`, `…_words.json` | `api.crossref.org/works?query.bibliographic=<the Min-K%++ title>&rows=5`, then the same with the first author and with the first six words | five unrelated items each: Crossref has no record of that paper |
| `crossref_search_empty.json` | `api.crossref.org/works?query.bibliographic=zzzzzzzz…&rows=5` | a real answer with zero items, which is what no record looks like |
| `crossref_doi_10.65215_ysbyhc05.json` | `api.crossref.org/works/10.65215/ysbyhc05` | the `posted-content` mirror of the 2017 paper: same title and authors, issued 2025, publisher "Shenzhen Medical Academy of Research and Translation" |
| `openalex_doi_10.1038_s41467-024-53632-4.json` | `api.openalex.org/works/doi:10.1038/s41467-024-53632-4` | identifiers, title, year, type, primary location, first three authorships, the first 14 positions of the inverted abstract |
| `openalex_doi_10.48550_arxiv.2505.21469.json` | `api.openalex.org/works/doi:10.48550/arxiv.2505.21469` | identifiers, title, year, type `preprint`, primary location, six authorships — this author list mixes `Zeng, Cheng` with `Jirui Jin` — and the first 14 positions of the inverted abstract |
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
