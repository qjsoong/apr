# Internal interfaces

The pipeline is three stages behind two function signatures. Each stage owns a
subpackage and is replaceable without touching the others.

```text
cli.py ──> pipeline.identify ──> extract.extract_local ──> PaperRecord
                            └──> providers.resolve     ──> PaperRecord
```

## `apr.model.PaperRecord` (shared, frozen)

- `set_file_info(path=, size=, sha256=, pages=)` — facts about the input file.
- `set_field(name, value, *, source, method, confidence, force=False, detail=None)`
  — offer a value; kept only if it beats the current one. Returns whether it was
  accepted. Empty values are dropped. Every considered value lands in
  `record.evidence`.
- `add_source(SourceAttempt(source, status, url=None, query=None, candidates=None, error=None))`
  — one network lookup. `status` is one of `ok`, `not-found`, `error`, `skipped`.
- `warn(message)` — user-visible note, deduplicated, surfaced as `warnings`.
- Only `apr.extract` and `apr.providers` write to a record; `cli.py` and
  `pipeline.py` read it.

Confidence is the merge rule, not the call order: local extraction uses 0.4–0.8,
an authoritative API record (arXiv by ID, Crossref by DOI) 0.9–1.0, a
bibliographic-search match 0.5–0.9 depending on similarity.

## `apr.extract.extract_local(path) -> PaperRecord`

Offline. Reads the file, never opens a socket. Raises `InputError` when the file
is missing, unreadable, or not a PDF. Must set `path`, `size`, `sha256`, `pages`
and everything the file alone reveals.

## `apr.providers.resolve(record, *, mailto, timeout, cache_dir, verbose) -> PaperRecord`

Online, only called with `--online`. Takes the record from extraction, consults
providers in order of evidence quality, offers fields through `set_field`, and
records every attempt with `add_source`. Network failures become
`SourceAttempt(status="error")`; `resolve` returns the record it was given
rather than raising.

## Provider rules

- No API keys: only endpoints that are free and keyless.
- `mailto` (from `--mailto`/`APR_MAILTO`) is appended where the service supports
  a polite pool; `User-Agent` names the tool and its repository.
- One request per provider per record unless the provider's own response
  requires a follow-up (e.g. Crossref search → work by DOI).
- `cache_dir is None` disables caching.
