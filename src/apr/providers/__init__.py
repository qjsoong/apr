"""Online resolution: ask scholarly APIs which paper this is.

Contract (see docs/INTERFACES.md):

    resolve(record, *, mailto, timeout, cache_dir, verbose) -> PaperRecord

Takes the record produced by local extraction, looks it up by identifier or by
bibliographic search, and offers every field it finds through
``record.set_field(...)`` so confidence decides the winner. Failures are
recorded as ``record.add_source(SourceAttempt(..., status=SOURCE_ERROR))``;
they never raise.

Providers are consulted in order of evidence, identifiers before titles, and
the run stops as soon as the record is complete enough to trust.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apr.match import (
    CONFIDENT_MATCH,
    CROSS_CHECK_THRESHOLD,
    MATCH_THRESHOLD,
    Candidate,
    cross_check,
    normalise_doi,
    parse_arxiv_id,
    score_candidate,
)
from apr.model import (
    SOURCE_ERROR,
    SOURCE_NOT_FOUND,
    SOURCE_OK,
    SOURCE_SKIPPED,
    PaperRecord,
    SourceAttempt,
)
from apr.providers import arxiv, crossref, datacite, openalex, semanticscholar
from apr.providers.http import HttpOptions

#: Most round trips one record may cost, retries included: the DOI chain alone
#: can use four, then arXiv by id and its Crossref follow-up.
MAX_REQUESTS = 6

#: Title confidence at which a complete record is good enough to stop asking.
HIGH_CONFIDENCE = 0.90

NOTHING_TO_LOOK_UP = "nothing to look up"

#: What a provider lookup returns: one candidate, several, or nothing at all.
Lookup = Callable[[], "Candidate | list[Candidate] | None"]


def resolve(
    record: PaperRecord,
    *,
    mailto: str | None = None,
    timeout: float = 10.0,
    cache_dir: Path | None = None,
    verbose: bool = False,
) -> PaperRecord:
    """Enrich a locally extracted record using online services.

    Never raises for a network or provider failure: every attempt, successful
    or not, is recorded in ``record.sources`` and the record is returned.
    A record resolved by identifier is cross-checked against the PDF text; a
    disagreement is reported as a warning, never used to veto the record.
    """
    options = HttpOptions(
        timeout=timeout, cache_dir=cache_dir, request_limit=MAX_REQUESTS
    )
    return _Resolution(record, options=options, mailto=mailto, verbose=verbose).run()


class _Resolution:
    """One record's journey through the providers, in order of evidence."""

    def __init__(
        self,
        record: PaperRecord,
        *,
        options: HttpOptions,
        mailto: str | None,
        verbose: bool,
    ) -> None:
        self.record = record
        self.options = options
        self.mailto = mailto
        self.verbose = verbose
        self._queried_dois: set[str] = set()
        #: What the PDF itself said, kept before any provider writes over it.
        self._pdf_title: str | None = None
        self._pdf_authors: list[str] = []

    # -- the plan ------------------------------------------------------------

    def run(self) -> PaperRecord:
        record = self.record
        doi = normalise_doi(record.doi)
        arxiv = parse_arxiv_id(record.arxiv_id)
        title = " ".join(record.title.split()) if record.title else None
        self._pdf_title = title
        self._pdf_authors = list(record.authors)

        if doi is None and arxiv is None and not title:
            record.add_source(
                SourceAttempt(
                    source="online", status=SOURCE_SKIPPED, error=NOTHING_TO_LOOK_UP
                )
            )
            return record

        if doi is not None:
            self._doi_chain(doi)
            if self._done():
                return record

        if arxiv is not None:
            self._arxiv_by_id(arxiv[0])
            if self._done():
                return record

        if title:
            self._search(title)

        return record

    def _doi_chain(self, doi: str) -> None:
        """DOI first: Crossref owns registered DOIs, then the fallbacks."""
        self._by_doi(
            "crossref",
            doi,
            lambda: crossref.by_doi(doi, mailto=self.mailto, options=self.options),
            authoritative=True,
        )
        if self._done():
            return
        self._by_doi(
            "openalex",
            doi,
            lambda: openalex.by_doi(doi, mailto=self.mailto, options=self.options),
        )
        if self._done():
            return
        self._by_doi(
            "datacite", doi, lambda: datacite.by_doi(doi, options=self.options)
        )
        if self._done():
            return
        self._by_doi(
            "semanticscholar",
            doi,
            lambda: semanticscholar.by_doi(doi, options=self.options),
        )

    def _by_doi(
        self, source: str, doi: str, call: Lookup, *, authoritative: bool = False
    ) -> None:
        self._queried_dois.add(doi)
        self._lookup(
            source,
            f"doi:{doi}",
            call,
            authoritative=authoritative,
            identifier=True,
            label=f"DOI {doi}",
        )

    def _arxiv_by_id(self, arxiv_id: str) -> None:
        candidate = self._lookup(
            "arxiv",
            f"id_list={arxiv_id}",
            lambda: arxiv.by_id(arxiv_id, options=self.options),
            authoritative=True,
            identifier=True,
            label=f"arXiv {arxiv_id}",
        )
        doi = candidate.doi if candidate else None
        if doi and doi not in self._queried_dois:
            # arXiv often knows the published DOI and journal reference; the
            # registered record behind them has the venue spelled out.
            self._by_doi(
                "crossref",
                doi,
                lambda: crossref.by_doi(doi, mailto=self.mailto, options=self.options),
                authoritative=True,
            )

    def _search(self, title: str) -> None:
        """Bibliographic search: every hit has to earn its place by score."""
        authors = self.record.authors
        steps: tuple[tuple[str, str, Lookup], ...] = (
            (
                "crossref",
                f"query.bibliographic={title}",
                lambda: crossref.search(
                    title, authors, mailto=self.mailto, options=self.options
                ),
            ),
            (
                "arxiv",
                f'ti:"{title}"',
                lambda: arxiv.search(title, authors, options=self.options),
            ),
            (
                "openalex",
                f"search={title}",
                lambda: openalex.search(
                    title, authors, mailto=self.mailto, options=self.options
                ),
            ),
            (
                "semanticscholar",
                f"query={title}",
                lambda: semanticscholar.match(title, options=self.options),
            ),
        )
        for source, query, lookup in steps:
            self._lookup(source, query, lookup)
            if self._done():
                return

    # -- one lookup ----------------------------------------------------------

    def _lookup(
        self,
        source: str,
        query: str,
        call: Lookup,
        *,
        authoritative: bool = False,
        identifier: bool = False,
        label: str | None = None,
    ) -> Candidate | None:
        """Run one lookup, record the attempt, offer what it found.

        ``label`` names an identifier the record was looked up by; it turns on
        the cross-check against the PDF text. Bibliographic candidates are not
        cross-checked: their score already decides whether they count.
        """
        if self.options.out_of_budget:
            self._log(f"{source}: skipped, request budget of {MAX_REQUESTS} spent")
            return None
        try:
            found = call()
        except Exception as exc:  # a provider must never break the run
            error = f"{type(exc).__name__}: {exc}"
            self.record.add_source(
                SourceAttempt(
                    source=source, status=SOURCE_ERROR, query=query, error=error
                )
            )
            self._log(f"{source}: {error}")
            return None

        candidates = _as_candidates(found)
        accepted = self._offer(source, query, candidates, authoritative, identifier)
        if label and accepted is not None:
            self._report_disagreement(label, accepted)
        self.record.add_source(
            SourceAttempt(
                source=source,
                status=SOURCE_OK if accepted is not None else SOURCE_NOT_FOUND,
                query=query,
                candidates=len(candidates),
            )
        )
        self._log(
            f"{source}: {'ok' if accepted is not None else 'not-found'} "
            f"({len(candidates)} candidates)"
        )
        return accepted

    def _offer(
        self,
        source: str,
        query: str,
        candidates: list[Candidate],
        authoritative: bool,
        identifier: bool,
    ) -> Candidate | None:
        """Offer the best candidate's fields; return it when it was accepted."""
        if not candidates:
            return None
        title = self._local_title()
        scored = [
            (
                score_candidate(
                    candidate,
                    title=title,
                    authors=self.record.authors,
                    year=self.record.year,
                ),
                candidate,
            )
            for candidate in candidates
        ]
        score, candidate = max(scored, key=lambda pair: pair[0])

        if authoritative:
            confidence = 1.0
        else:
            if identifier and title is None:
                # Nothing in the file contradicts the identifier, so it is all
                # the evidence there is; a search hit has to earn its score.
                score = 1.0
            elif not identifier and score < MATCH_THRESHOLD:
                self._log(
                    f"{source}: best candidate scored {score:.2f}, "
                    f"below {MATCH_THRESHOLD}"
                )
                return None
            confidence = 0.5 + 0.5 * score
            if score < CONFIDENT_MATCH:
                self.record.warn(
                    f"{source} matched at {score:.2f} (below {CONFIDENT_MATCH}); "
                    "check the title, authors and identifiers"
                )

        for name, value in _candidate_fields(candidate):
            self.record.set_field(
                name, value, source=source, method=query, confidence=confidence
            )
        return candidate

    # -- helpers -------------------------------------------------------------

    def _report_disagreement(self, label: str, candidate: Candidate) -> None:
        """Warn when a looked-up record does not look like the PDF's paper.

        Both values are already in the evidence log - the PDF's title from
        extraction, the looked-up title from the provider - so ``--why`` shows
        what disagreed. Confidence still decides the winner: this only tells the
        user that the identifier and the file may not belong together.
        """
        agreement = cross_check(
            candidate, title=self._pdf_title, authors=self._pdf_authors
        )
        if agreement is None or agreement >= CROSS_CHECK_THRESHOLD:
            return
        self.record.warn(
            f"looked-up record for {label} disagrees with the PDF text "
            f"(similarity {agreement:.2f}); the PDF may carry a stale identifier"
        )

    def _local_title(self) -> str | None:
        title = self.record.title
        return " ".join(title.split()) if title and title.strip() else None

    def _done(self) -> bool:
        """True when the record is complete and confident enough to stop."""
        record = self.record
        complete = bool(
            record.title
            and record.authors
            and record.year
            and (record.doi or record.arxiv_id)
        )
        if not complete:
            return False
        provenance = record.provenance.get("title")
        return provenance is not None and provenance.confidence >= HIGH_CONFIDENCE

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"apr: {message}", file=sys.stderr)


def _as_candidates(found: Candidate | list[Candidate] | None) -> list[Candidate]:
    if found is None:
        return []
    if isinstance(found, Candidate):
        return [found]
    return list(found)


def _candidate_fields(candidate: Candidate) -> list[tuple[str, Any]]:
    """The record fields a candidate can fill, in schema order."""
    return [
        (name, value)
        for name, value in (
            ("title", candidate.title),
            ("authors", candidate.authors),
            ("year", candidate.year),
            ("venue", candidate.venue),
            ("doi", candidate.doi),
            ("arxiv_id", candidate.arxiv_id),
            ("arxiv_version", candidate.arxiv_version),
            ("url", candidate.url),
            ("abstract", candidate.abstract),
        )
        if value
    ]
