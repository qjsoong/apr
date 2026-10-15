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
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from apr.match import (
    CONFIDENT_MATCH,
    CROSS_CHECK_THRESHOLD,
    MATCH_THRESHOLD,
    YEAR_DISTANCE,
    Candidate,
    best_candidate,
    cross_check,
    normalise_arxiv_version,
    normalise_doi,
    parse_arxiv_id,
    score_candidate,
    surname,
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

#: A posting may fill a gap, but it never outranks what the PDF itself showed:
#: the conference header or journal line printed on the paper is better evidence
#: of the venue and year than the preprint server the authors also used.
PREPRINT_CONFIDENCE = 0.4

#: Least confidence a local year needs before it is worth defending.
LOCAL_YEAR_FLOOR = 0.4

NOTHING_TO_LOOK_UP = "nothing to look up"

#: Words of a title used for the last Crossref retry.
TITLE_WORDS = 6

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
        self._pdf_year: int | None = None
        self._pdf_year_confidence = 0.0
        self._pdf_venue: str | None = None
        self._pdf_arxiv_version: str | None = None

    # -- the plan ------------------------------------------------------------

    def run(self) -> PaperRecord:
        record = self.record
        doi = normalise_doi(record.doi)
        arxiv = parse_arxiv_id(record.arxiv_id)
        title = " ".join(record.title.split()) if record.title else None
        self._pdf_title = title
        self._pdf_authors = list(record.authors)
        self._pdf_year = record.year
        self._pdf_venue = record.venue
        self._pdf_arxiv_version = (
            arxiv[1] if arxiv else None
        ) or normalise_arxiv_version(record.arxiv_version)
        local_year = record.provenance.get("year")
        self._pdf_year_confidence = local_year.confidence if local_year else 0.0

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
            lambda: arxiv.by_id(
                arxiv_id,
                reported_version=self._pdf_arxiv_version,
                options=self.options,
            ),
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
        """Bibliographic search: every hit has to earn its place by score.

        Crossref is asked with the plain title first; only when that yields
        nothing acceptable is it asked again, narrowed by the first author and
        then by the first few words, so a publisher's long title with a subtitle
        does not lose the record it belongs to. The exact request URL of each
        attempt is recorded, and every attempt is one of the six requests a
        record may cost.
        """
        authors = self.record.authors
        for query in _crossref_queries(title, authors):
            self._lookup(
                "crossref",
                f"query.bibliographic={query}",
                lambda query=query: crossref.search(
                    query, mailto=self.mailto, options=self.options
                ),
                url=crossref.bibliography_url(query, mailto=self.mailto),
            )
            if self._done():
                return

        steps: tuple[tuple[str, str, Lookup], ...] = (
            (
                "arxiv",
                f'ti:"{title}"',
                lambda: arxiv.search(
                    title,
                    authors,
                    reported_version=self._pdf_arxiv_version,
                    options=self.options,
                ),
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
        url: str | None = None,
    ) -> Candidate | None:
        """Run one lookup, record the attempt, offer what it found.

        ``label`` names an identifier the record was looked up by; it turns on
        the cross-check against the PDF text. Bibliographic candidates are not
        cross-checked: their score already decides whether they count. ``url``
        is the request the caller made, for the source log.
        """
        if self.options.out_of_budget:
            # Say so in the machine-readable record, not only on stderr: a caller
            # must be able to tell "this provider had nothing" from "apr stopped
            # asking", which is what a spent request budget means.
            self._log(f"{source}: skipped, request budget of {MAX_REQUESTS} spent")
            self.record.add_source(
                SourceAttempt(
                    source=source,
                    status=SOURCE_SKIPPED,
                    query=query,
                    error=f"request budget of {MAX_REQUESTS} spent",
                )
            )
            return None
        try:
            found = call()
        except Exception as exc:  # a provider must never break the run
            error = f"{type(exc).__name__}: {exc}"
            self.record.add_source(
                SourceAttempt(
                    source=source,
                    status=SOURCE_ERROR,
                    query=query,
                    url=url,
                    error=error,
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
                url=url,
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
        score, candidate = best_candidate(scored)

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
            if name in self._fields_a_different_edition_may_not_take(candidate):
                continue
            self.record.set_field(
                name,
                value,
                source=source,
                method=query,
                confidence=self._field_confidence(name, candidate, confidence),
            )
        self._warn_about_record(source, candidate)
        if candidate.note:
            self.record.warn(candidate.note)
        return candidate

    # -- helpers -------------------------------------------------------------

    def _field_confidence(
        self, name: str, candidate: Candidate, confidence: float
    ) -> float:
        """A posting may fill a gap, but never outrank what the PDF showed."""
        if candidate.is_preprint and name in ("venue", "year"):
            return min(confidence, PREPRINT_CONFIDENCE)
        return confidence

    def _local_year(self) -> int | None:
        """The year the PDF itself gave, when it is worth defending."""
        if self._pdf_year is None or self._pdf_year_confidence < LOCAL_YEAR_FLOOR:
            return None
        return self._pdf_year

    def _year_is_too_far(self, candidate: Candidate) -> bool:
        """True when the candidate's year cannot be the year of this paper."""
        local = self._local_year()
        if local is None or candidate.year is None:
            return False
        return abs(local - candidate.year) > YEAR_DISTANCE

    def _fields_a_different_edition_may_not_take(
        self, candidate: Candidate
    ) -> tuple[str, ...]:
        """Fields a record years away from the local year may not contribute.

        Such a record is a different edition of the paper - a mirror, a
        re-deposit, a later reprint. It may still say which paper this is (its
        title, its authors, its identifiers), but its year, venue and DOI belong
        to that edition, not to the file being read.
        """
        if not self._year_is_too_far(candidate):
            return ()
        return ("year", "venue", "doi", "url")

    def _warn_about_record(self, source: str, candidate: Candidate) -> None:
        """Say so when a record disagrees with what the PDF itself showed.

        A posting is not allowed to overwrite the venue or year printed on the
        paper, and a record years away from the local year is a different
        edition of the paper. Both are reported even though the fields they
        touch were already refused or capped.
        """
        local_year, local_venue = self._local_year(), self._pdf_venue
        if candidate.is_preprint:
            if (
                local_venue
                and candidate.venue
                and local_venue.casefold() != candidate.venue.casefold()
            ):
                matched = (
                    f"a {candidate.year} preprint" if candidate.year else "a preprint"
                )
                self.record.warn(
                    f"local venue suggests {local_venue}; "
                    f"the matched record is {matched}"
                )
            if (
                local_year is not None
                and candidate.year is not None
                and local_year != candidate.year
            ):
                self.record.warn(
                    f"local year {local_year} disagrees with the "
                    f"{candidate.year} preprint record"
                )
            return
        if self._year_is_too_far(candidate):
            self.record.warn(
                f"local year {local_year} disagrees with the {candidate.year} "
                f"record from {source}; keeping the local year"
            )

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
        # A machine-readable flag next to the warning: a caller that never looks
        # at human-readable warnings can still refuse to trust this record.
        self.record.disputed = True

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


def _crossref_queries(title: str, authors: Sequence[str]) -> list[str]:
    """The bibliographic queries to try, in order, until one is acceptable.

    The plain title first: it is what a publisher's own record is registered
    under. Only when that finds nothing is the first author added, and then the
    first few words - a title with a long subtitle is still a match on its first
    half.
    """
    queries = [title]
    first_author = surname(authors[0]) if authors else ""
    if first_author:
        queries.append(f"{title} {first_author}")
    shortened = " ".join(title.split()[:TITLE_WORDS])
    if shortened and shortened != title:
        queries.append(shortened)
    return queries


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
