# Contributing to apr

## Setup

```console
git clone https://github.com/qjsoong/apr && cd apr
uv sync
```

Python 3.10+ is required; `uv` manages the environment and the lockfile
(`uv.lock` is committed, and CI uses `--locked`).

## The checks CI runs

```console
uv run ruff format --check .   # formatting
uv run ruff check .            # lint
uv run ty check                # types
uv run pytest                  # tests, on 3.10 / 3.11 / 3.12 / 3.13
uv build                       # sdist + wheel, installed and run in CI
```

Two checks deliberately do not run in CI, because one needs a corpus of
copyrighted PDFs and the other needs the network:

```console
APR_CORPUS=~/doc/paper uv run pytest                  # corpus tests + corpus scoreboard
APR_TEST_ALLOW_NETWORK=1 uv run python scripts/live_smoke.py   # live lookups of known papers
```

Run the same commands before opening a pull request. Formatting is not
negotiated: `uv run ruff format .` is the answer.

## Rules the tests enforce

- **No network in tests.** `tests/conftest.py` replaces `socket.connect`,
  `socket.connect_ex`, `socket.create_connection` and `socket.getaddrinfo` with
  a guard that raises, and `tests/test_network_guard.py` asserts the guard is
  live. Its scope is the in-process path: a subprocess (for example the CLI test
  that runs `python -m apr`) is not covered by it, which is why the corpus
  evaluation is also run inside an empty network namespace.
- **No PDFs in the repository.** Real papers are copyrighted. Unit tests build
  their own PDFs in `tests/fixtures/` with `scripts/make_fixtures.py`, which
  writes raw PDF syntax and needs no extra dependency. Evaluation against real
  papers expects a local corpus (`APR_CORPUS`), which is never committed.
- **Output schema is a contract.** `docs/schema.md` is what callers program
  against. Adding a field is a minor change; renaming or removing one is a
  breaking change and bumps `schema_version`.
- **Provenance is mandatory.** Every value that reaches the output goes through
  `record.set_field(..., source=..., method=..., confidence=...)`, so any line
  of output can be traced to where it came from.

## Adding a data source

1. Add a module in `src/apr/providers/` exposing low-level lookups that return
   parsed candidates; keep HTTP concerns in `providers/http.py`.
2. Offer fields with `record.set_field(...)` and record the attempt with
   `record.add_source(SourceAttempt(...))` — including failures.
3. Use public, keyless endpoints, honour `--mailto` where the service asks for a
   contact address, and cache responses.
4. Add a test with a recorded response; do not make the suite depend on a live
   service.

## Scope

`apr` identifies papers. Reference management, renaming, downloading and BibTeX
formatting are deliberately out of scope — see the non-goals in the README and
in [notes/CONTRACT.md](notes/CONTRACT.md). A pull request that adds one of them
will be declined with a pointer to this paragraph, not on its merits.
