"""Tests for apr.providers.resolve: the whole lookup journey, no socket.

Recorded responses are routed by URL fragment through the real
``apr.providers.http`` layer, so retries, the cache, the request budget and the
host pacing are exercised too. Only the opener and the waiting are replaced.
"""

from __future__ import annotations

import functools
import json
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from apr.match import CONFIDENT_MATCH, CROSS_CHECK_THRESHOLD, title_score
from apr.model import (
    SOURCE_ERROR,
    SOURCE_NOT_FOUND,
    SOURCE_OK,
    SOURCE_SKIPPED,
    PaperRecord,
)
from apr.providers import HttpOptions, crossref, http, resolve

RECORDED = Path(__file__).parent / "recorded"

MEMBERSHIP_TITLE = (
    "Towards More Realistic Membership Inference Attacks on Large Diffusion Models"
)

TAMGEN_TITLE = (
    "TamGen: drug design with target-aware molecule generation through a "
    "chemical language model"
)


def recorded(name: str) -> str:
    return (RECORDED / name).read_text("utf-8")


class Online:
    """A fake network: recorded bodies, routed by URL fragment, in order."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, int, bytes | Exception]] = []
        self.requests: list[urllib.request.Request] = []
        self.slept: list[float] = []

    def file(self, fragment: str, name: str, *, status: int = 200) -> None:
        self.text(fragment, recorded(name), status=status)

    def text(self, fragment: str, body: str, *, status: int = 200) -> None:
        self.routes.append((fragment, status, body.encode("utf-8")))

    def json(self, fragment: str, payload: object, *, status: int = 200) -> None:
        self.text(fragment, json.dumps(payload), status=status)

    def broken(self, fragment: str, body: str = '{"status": "ok"') -> None:
        """Answer 200 with a body that is not valid JSON."""
        self.text(fragment, body)

    def fail(self, fragment: str, error: Exception) -> None:
        self.routes.append((fragment, 0, error))

    @property
    def urls(self) -> list[str]:
        return [request.full_url for request in self.requests]

    def host(self, url: str) -> str:
        return urllib.parse.urlsplit(url).netloc

    def open(
        self, request: urllib.request.Request, timeout: float
    ) -> tuple[int, bytes, dict[str, str]]:
        self.requests.append(request)
        for fragment, status, body in self.routes:
            if fragment in request.full_url:
                if isinstance(body, Exception):
                    raise body
                return status, body, {}
        raise AssertionError(f"unexpected request: {request.full_url}")


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> Online:
    online = Online()
    monkeypatch.setattr(http, "open_request", online.open)
    # The same options class, with the waiting recorded instead of performed.
    monkeypatch.setattr(
        "apr.providers.HttpOptions",
        functools.partial(HttpOptions, sleeper=online.slept.append),
    )
    return online


def local_record(**fields: tuple[object, float]) -> PaperRecord:
    """A record as extraction would hand it over: a value and a confidence."""
    record = PaperRecord(path="paper.pdf")
    for name, (value, confidence) in fields.items():
        record.set_field(
            name,
            value,
            source="pdf-first-page",
            method="layout",
            confidence=confidence,
        )
    return record


def sources(record: PaperRecord) -> list[tuple[str, str]]:
    return [(attempt.source, attempt.status) for attempt in record.sources]


# -- identifiers ------------------------------------------------------------


def test_a_doi_is_resolved_by_crossref_and_nothing_else_is_asked(
    online: Online,
) -> None:
    online.file(
        "api.crossref.org/works/10.1038/s41467",
        "crossref_doi_10.1038_s41467-024-53632-4.json",
    )
    record = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))

    result = resolve(record)

    assert result is record
    assert record.title == (
        "TamGen: drug design with target-aware molecule generation through a "
        "chemical language model"
    )
    assert record.authors[0] == "Kehan Wu"
    assert record.year == 2024
    assert record.venue == "Nature Communications"
    assert record.doi == "10.1038/s41467-024-53632-4"
    assert record.url == "https://doi.org/10.1038/s41467-024-53632-4"
    assert record.provenance["title"].confidence == 1.0
    assert record.provenance["title"].source == "crossref"
    assert sources(record) == [("crossref", SOURCE_OK)]
    assert record.sources[0].query == "doi:10.1038/s41467-024-53632-4"
    assert record.sources[0].candidates == 1
    assert record.warnings == []  # the PDF gave no title, so nothing to cross-check
    assert online.urls == ["https://api.crossref.org/works/10.1038/s41467-024-53632-4"]
    assert online.requests[0].get_header("User-agent") == http.USER_AGENT


def test_the_normalised_doi_is_what_gets_looked_up(online: Online) -> None:
    online.file(
        "api.crossref.org/works/10.1038/s41467",
        "crossref_doi_10.1038_s41467-024-53632-4.json",
    )
    record = local_record(doi=("https://doi.org/10.1038/S41467-024-53632-4.", 0.7))

    resolve(record)

    assert online.urls == ["https://api.crossref.org/works/10.1038/s41467-024-53632-4"]
    assert record.doi == "10.1038/s41467-024-53632-4"


def test_the_polite_address_travels_in_the_query(online: Online) -> None:
    online.file(
        "api.crossref.org/works/10.1038/s41467",
        "crossref_doi_10.1038_s41467-024-53632-4.json",
    )
    record = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))

    resolve(record, mailto="me@example.com")

    assert "mailto=me%40example.com" in online.urls[0]


def test_openalex_answers_when_crossref_does_not_have_the_doi(
    online: Online,
) -> None:
    online.json("api.crossref.org/works/", {"status": "ok"}, status=404)
    online.file(
        "api.openalex.org/works/doi:", "openalex_doi_10.1038_s41467-024-53632-4.json"
    )
    record = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))

    resolve(record)

    assert sources(record) == [
        ("crossref", SOURCE_NOT_FOUND),
        ("openalex", SOURCE_OK),
    ]
    assert (record.title or "").startswith("TamGen")
    assert record.provenance["title"].source == "openalex"
    # The file had no title to contradict the DOI, so the identifier stands.
    assert record.provenance["title"].confidence == 1.0


def test_datacite_answers_when_neither_registry_does(online: Online) -> None:
    online.json("api.crossref.org/works/", {"status": "ok"}, status=404)
    online.json("api.openalex.org/works/doi:", {"error": "not found"}, status=404)
    online.file("api.datacite.org/dois/", "datacite_doi_10.5281_zenodo.18289746.json")
    record = local_record(doi=("10.5281/zenodo.18289746", 0.8))

    resolve(record)

    assert sources(record)[-1] == ("datacite", SOURCE_OK)
    assert record.title == "Attention is all you need"
    assert record.authors[0] == "Beat Döbeli Honegger"
    assert record.year == 2026


def test_arxiv_by_id_fills_what_the_pdf_does_not_have(online: Online) -> None:
    online.file(
        "export.arxiv.org/api/query?id_list=2306.12983", "arxiv_id_2306.12983.xml"
    )
    record = local_record(arxiv_id=("2306.12983", 0.8))

    resolve(record)

    assert record.title == MEMBERSHIP_TITLE
    assert record.authors[:2] == ["Jan Dubiński", "Antoni Kowalczuk"]
    assert record.year == 2023
    assert record.venue == "arXiv"
    assert record.arxiv_id == "2306.12983"
    assert record.arxiv_version == "v2"
    assert record.url == "https://arxiv.org/abs/2306.12983v2"
    assert record.provenance["title"].confidence == 1.0
    assert record.warnings == []  # the PDF gave no title, so nothing to cross-check
    assert sources(record) == [("arxiv", SOURCE_OK)]
    assert online.urls == [
        "https://export.arxiv.org/api/query?id_list=2306.12983&max_results=1"
    ]


def test_an_identifier_lookup_that_agrees_with_the_pdf_stays_quiet(
    online: Online,
) -> None:
    online.file(
        "api.crossref.org/works/10.1038/s41467",
        "crossref_doi_10.1038_s41467-024-53632-4.json",
    )
    record = local_record(
        title=(TAMGEN_TITLE, 0.7),
        authors=(["Kehan Wu"], 0.6),
        doi=("10.1038/s41467-024-53632-4", 0.6),
    )

    resolve(record)

    assert record.title == TAMGEN_TITLE
    assert record.authors[0] == "Kehan Wu"
    assert record.provenance["title"].source == "crossref"
    assert record.warnings == []
    assert sources(record) == [("crossref", SOURCE_OK)]


def test_a_doi_that_disagrees_with_the_pdf_title_is_reported(
    online: Online,
) -> None:
    """A stale DOI: the PDF says TamGen, the DOI belongs to another paper."""
    online.file(
        "api.crossref.org/works/", "crossref_doi_10.1038_s41467-025-60763-9.json"
    )
    resolved = json.loads(recorded("crossref_doi_10.1038_s41467-025-60763-9.json"))[
        "message"
    ]["title"][0]
    record = local_record(
        title=(TAMGEN_TITLE, 0.8), doi=("10.1038/s41467-025-60763-9", 0.6)
    )

    resolve(record)

    # The identifier is still authoritative for that identifier: it wins.
    assert record.title == resolved
    assert record.doi == "10.1038/s41467-025-60763-9"
    assert record.provenance["title"].source == "crossref"
    # And the disagreement is reported, with the number behind it.
    agreement = title_score(TAMGEN_TITLE, resolved)
    assert agreement < CROSS_CHECK_THRESHOLD
    assert record.warnings == [
        f"looked-up record for DOI 10.1038/s41467-025-60763-9 disagrees with the "
        f"PDF text (similarity {agreement:.2f}); the PDF may carry a stale "
        "identifier"
    ]
    # Both values stay in the evidence log, each with its source.
    titles = {
        (item.source, item.value) for item in record.evidence if item.field == "title"
    }
    assert ("pdf-first-page", TAMGEN_TITLE) in titles
    assert ("crossref", resolved) in titles
    assert sources(record) == [("crossref", SOURCE_OK)]


def test_an_arxiv_id_that_disagrees_with_the_pdf_title_is_reported(
    online: Online,
) -> None:
    online.file(
        "export.arxiv.org/api/query?id_list=2306.12983", "arxiv_id_2306.12983.xml"
    )
    record = local_record(
        arxiv_id=("2306.12983", 0.6),
        title=("A survey of protein folding with transformers", 0.7),
    )

    resolve(record)

    assert record.title == MEMBERSHIP_TITLE  # the id still wins
    agreement = title_score(
        "A survey of protein folding with transformers", MEMBERSHIP_TITLE
    )
    assert agreement < CROSS_CHECK_THRESHOLD
    assert record.warnings == [
        f"looked-up record for arXiv 2306.12983 disagrees with the PDF text "
        f"(similarity {agreement:.2f}); the PDF may carry a stale identifier"
    ]
    assert sources(record) == [("arxiv", SOURCE_OK)]


def test_arxiv_by_id_follows_its_published_doi_to_crossref(online: Online) -> None:
    online.file(
        "export.arxiv.org/api/query?id_list=2105.04565", "arxiv_id_2105.04565.xml"
    )
    online.file(
        "api.crossref.org/works/10.1103/physrevd.104.095029",
        "crossref_doi_10.1103_physrevd.104.095029.json",
    )
    record = local_record(arxiv_id=("2105.04565", 0.8))

    resolve(record)

    assert sources(record) == [("arxiv", SOURCE_OK), ("crossref", SOURCE_OK)]
    assert online.urls[1] == (
        "https://api.crossref.org/works/10.1103/physrevd.104.095029"
    )
    assert record.doi == "10.1103/physrevd.104.095029"
    assert record.venue == "Phys. Rev. D 104 (2021) 095029"
    # Both answered at confidence 1.0, so the first value stands.
    assert record.provenance["venue"].source == "arxiv"
    assert record.title == "Dark photon limits: a handbook"
    assert len(online.urls) == 2


def test_a_doi_the_record_already_had_is_not_queried_twice(online: Online) -> None:
    """arXiv may report the DOI the record came in with."""
    online.file(
        "export.arxiv.org/api/query?id_list=2105.04565", "arxiv_id_2105.04565.xml"
    )
    online.file(
        "api.crossref.org/works/10.1103/physrevd.104.095029",
        "crossref_doi_10.1103_physrevd.104.095029.json",
    )
    record = local_record(
        doi=("10.1103/PhysRevD.104.095029", 0.8), arxiv_id=("2105.04565", 0.8)
    )

    resolve(record)

    crossref_calls = [url for url in online.urls if "api.crossref.org" in url]
    assert len(crossref_calls) == 1


# -- bibliographic search ---------------------------------------------------


def test_the_search_picks_the_right_candidate_and_ignores_the_near_misses(
    online: Online,
) -> None:
    online.file("api.crossref.org/works?", "crossref_search_membership_inference.json")
    record = local_record(
        title=(MEMBERSHIP_TITLE, 0.8), authors=(["Jan Dubiński"], 0.7)
    )

    resolve(record)

    assert record.doi == "10.1109/wacv57701.2024.00479"
    assert record.title == MEMBERSHIP_TITLE
    assert record.authors[0] == "Jan Dubiński"
    assert record.year == 2024
    assert record.venue is not None and "WACV" in record.venue
    assert record.warnings == []
    assert record.provenance["title"].source == "crossref"
    assert record.provenance["title"].confidence == 1.0
    assert record.sources[0].candidates == 4
    assert record.sources[0].status == SOURCE_OK
    assert sources(record) == [("crossref", SOURCE_OK)]
    assert len(online.urls) == 1
    for near_miss in (
        "10.5220/0013182600003912",
        "10.1109/spw59333.2023.00013",
        "10.14722/ndss.2025.230324",
    ):
        assert near_miss not in json.dumps(record.to_dict())


def test_the_plain_title_query_finds_the_wacv_record(online: Online) -> None:
    """Crossref has the paper; the plain-title query is what finds it.

    The old contract narrowed that first query with the first author, which is
    what lost this record; the new one asks the plain title with five rows and
    no date filter, and only narrows when nothing acceptable comes back.
    """
    online.file(
        "api.crossref.org/works?", "crossref_search_membership_inference_plain.json"
    )
    record = local_record(
        title=(MEMBERSHIP_TITLE, 0.8),
        authors=(["Jan Dubinski"], 0.7),
        year=(2024, 0.4),
        venue=("IEEE Winter Conference on Applications of Computer Vision", 0.65),
    )

    resolve(record)

    assert record.doi == "10.1109/wacv57701.2024.00479"
    assert record.year == 2024
    assert record.provenance["title"].confidence == 1.0
    # The exact request is in the source log, with no author and no date filter.
    attempt = record.sources[0]
    assert attempt.query == f"query.bibliographic={MEMBERSHIP_TITLE}"
    assert attempt.url == crossref.bibliography_url(MEMBERSHIP_TITLE)
    assert "query.author" not in (attempt.url or "")
    assert "filter" not in (attempt.url or "")
    assert "rows=5" in (attempt.url or "")
    assert len(online.urls) == 1


def test_crossref_falls_back_to_the_author_then_the_first_words(
    online: Online,
) -> None:
    """Nothing acceptable for the plain title, so the query is narrowed.

    The first retry adds the first author's surname to the same query string -
    not the old ``query.author`` parameter, and still with no date filter.
    """
    online.file("Dubi%C5%84ski", "crossref_search_membership_inference.json")
    online.file(
        "query.bibliographic=Membership+Inference", "crossref_search_empty.json"
    )
    title = "Membership Inference Attacks on Large Diffusion Models"
    record = local_record(title=(title, 0.8), authors=(["Jan Dubiński"], 0.7))

    resolve(record)

    assert online.urls[0] == crossref.bibliography_url(title)
    assert online.urls[1] == crossref.bibliography_url(f"{title} Dubiński")
    assert "query.author" not in online.urls[1]
    assert "filter" not in online.urls[1]
    assert sources(record) == [
        ("crossref", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_OK),
    ]
    assert record.doi == "10.1109/wacv57701.2024.00479"
    assert record.sources[0].url == online.urls[0]
    assert record.sources[1].url == online.urls[1]


def test_the_crossref_queries_narrow_from_the_title_to_the_author_to_six_words(
    online: Online,
) -> None:
    """The three queries in order, each with its exact URL recorded.

    The Min-K%++ paper is not in Crossref, so all three come back with unrelated
    works and are turned down before the arXiv search identifies the paper.
    """
    title = (
        "Min-K%++: Improved Baseline for Detecting Pre-Training Data from Large "
        "Language Models"
    )
    # Registered most specific first: the author and six-word retries before the
    # plain title, because each URL contains the one before it.
    online.file("+Zhang&rows=5", "crossref_search_min_k_prime_author.json")
    online.file("+Pre-Training&rows=5", "crossref_search_min_k_prime_words.json")
    online.file("query.bibliographic=", "crossref_search_min_k_prime_plain.json")
    online.file("export.arxiv.org/api/query", "arxiv_search_min_k_prime.xml")
    record = local_record(title=(title, 0.8), authors=(["Jingyang Zhang"], 0.7))

    resolve(record)

    six_words = " ".join(title.split()[:6])
    assert [attempt.query for attempt in record.sources[:3]] == [
        f"query.bibliographic={title}",
        f"query.bibliographic={title} Zhang",
        f"query.bibliographic={six_words}",
    ]
    assert [attempt.url for attempt in record.sources[:3]] == [
        crossref.bibliography_url(title),
        crossref.bibliography_url(f"{title} Zhang"),
        crossref.bibliography_url(six_words),
    ]
    assert all(
        "filter" not in (attempt.url or "")
        and "query.author" not in (attempt.url or "")
        for attempt in record.sources[:3]
    )
    assert sources(record)[:3] == [
        ("crossref", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_NOT_FOUND),
    ]
    assert sources(record)[3] == ("arxiv", SOURCE_OK)  # the preprint identifies it


def test_a_weak_match_is_offered_with_a_warning(online: Online) -> None:
    """A paraphrase scores between the gate and the confident bar.

    The two bars are part of the contract and the scoring changes (year
    proximity, published over posted) must not move them: 0.60 to count at all,
    0.85 to count as confident. A candidate between the two is still offered,
    and the warning names the source and the score.
    """
    online.file(
        "api.crossref.org/works?", "crossref_search_membership_inference_plain.json"
    )
    online.text(
        "export.arxiv.org/api/query",
        "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'/>",
    )
    online.json("api.openalex.org/works?", {"results": []})
    online.json("api.semanticscholar.org", {"data": []})
    local = "Membership Inference Attacks on Large Diffusion"
    record = local_record(title=(local, 0.5))

    resolve(record)

    # A six-word title is the whole title, so Crossref is asked once.
    score = title_score(local, MEMBERSHIP_TITLE)
    assert 0.6 <= score < CONFIDENT_MATCH
    assert record.title == MEMBERSHIP_TITLE
    title = record.provenance["title"]
    assert title.source == "crossref"
    assert title.confidence == pytest.approx(0.5 + 0.5 * score)
    assert record.warnings == [
        f"crossref matched at {score:.2f} (below {CONFIDENT_MATCH}); "
        "check the title, authors and identifiers"
    ]
    # The cross-check watches identifiers only; a search hit is judged by score.
    assert "stale identifier" not in record.warnings[0]
    assert sources(record) == [
        ("crossref", SOURCE_OK),
        ("arxiv", SOURCE_NOT_FOUND),
        ("openalex", SOURCE_NOT_FOUND),
        ("semanticscholar", SOURCE_NOT_FOUND),
    ]


def test_a_search_with_only_unrelated_candidates_leaves_the_record_alone(
    online: Online,
) -> None:
    """Every candidate scores under the 0.60 gate: nothing may be contributed.

    Not one field changes, every attempt is recorded as not-found, and the only
    entries in the evidence log stay the ones extraction made.
    """
    online.file("api.crossref.org/works?", "crossref_search_min_k_prime_plain.json")
    online.text(
        "export.arxiv.org/api/query",
        "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'/>",
    )
    online.file("api.openalex.org/works?", "openalex_search_attention.json")
    online.file("api.semanticscholar.org", "semanticscholar_match_attention.json")
    record = local_record(
        title=("A survey of protein folding with transformers", 0.7),
        authors=(["Someone Else"], 0.6),
    )

    resolve(record)

    assert record.title == "A survey of protein folding with transformers"
    assert record.authors == ["Someone Else"]
    assert record.doi is None
    assert record.year is None
    assert record.provenance["title"].source == "pdf-first-page"
    assert record.warnings == []
    # Nothing but extraction ever offered a value: the gate held for every
    # candidate, so not even a rejected value reached the evidence log.
    assert {item.source for item in record.evidence} == {"pdf-first-page"}
    # The plain title, the title with the first author, then its first six
    # words: all three answered with unrelated works, so all three were turned
    # down and the other providers were asked as well.
    assert sources(record) == [
        ("crossref", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_NOT_FOUND),
        ("arxiv", SOURCE_NOT_FOUND),
        ("openalex", SOURCE_NOT_FOUND),
        ("semanticscholar", SOURCE_NOT_FOUND),
    ]
    assert [attempt.candidates for attempt in record.sources] == [5, 5, 5, 0, 5, 1]


def test_a_preprint_does_not_override_the_venue_and_year_of_an_iclr_paper(
    online: Online,
) -> None:
    """The PDF prints "Published as a conference paper at ICLR 2025".

    The arXiv preprint says 2024 and arXiv; neither may take the paper's own
    venue and year, and both disagreements are reported.
    """
    title = (
        "Min-K%++: Improved Baseline for Detecting Pre-Training Data from Large "
        "Language Models"
    )
    online.file(
        "crossref.org/works?query.bibliographic=MIN-K",
        "crossref_search_min_k_prime_plain.json",
    )
    online.file(
        "crossref.org/works?query.bibliographic=MIN-K%25%2B%25",
        "crossref_search_min_k_prime_words.json",
    )
    online.file(
        "crossref.org/works?query.bibliographic=",
        "crossref_search_min_k_prime_author.json",
    )
    online.file("export.arxiv.org/api/query", "arxiv_search_min_k_prime.xml")
    record = local_record(
        title=(title, 0.8),
        authors=(["Jingyang Zhang"], 0.7),
        venue=("ICLR 2025", 0.7),
        year=(2025, 0.7),
    )

    resolve(record)

    assert record.venue == "ICLR 2025"
    assert record.year == 2025
    assert record.provenance["venue"].source == "pdf-first-page"
    assert record.provenance["year"].source == "pdf-first-page"
    assert record.arxiv_id == "2404.02936"  # the preprint still identifies the paper
    assert record.warnings == [
        "local venue suggests ICLR 2025; the matched record is a 2024 preprint",
        "local year 2025 disagrees with the 2024 preprint record",
    ]
    assert sources(record) == [
        ("crossref", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_NOT_FOUND),
        ("arxiv", SOURCE_OK),
    ]


def test_a_preprint_does_not_override_a_year_taken_from_the_file_name(
    online: Online,
) -> None:
    """The WACV file is named ..._WACV_2024_...; the preprint is from 2023."""
    online.file("api.crossref.org/works?", "crossref_search_empty.json")
    online.text(
        "export.arxiv.org/api/query",
        recorded("arxiv_search_membership_inference.xml"),
    )
    record = local_record(
        title=(MEMBERSHIP_TITLE, 0.8),
        authors=(["Jan Dubinski"], 0.7),
        year=(2024, 0.4),
        venue=("IEEE Winter Conference on Applications of Computer Vision", 0.65),
    )

    resolve(record)

    assert record.year == 2024
    assert record.venue == "IEEE Winter Conference on Applications of Computer Vision"
    assert record.arxiv_id == "2306.12983"
    assert record.warnings == [
        "local venue suggests IEEE Winter Conference on Applications of Computer "
        "Vision; the matched record is a 2023 preprint",
        "local year 2024 disagrees with the 2023 preprint record",
    ]


def test_a_record_years_away_does_not_take_the_local_year(online: Online) -> None:
    """A 2025 posting of the 2017 paper may identify it, never re-date it."""
    online.file("api.crossref.org/works/", "crossref_doi_10.65215_ysbyhc05.json")
    record = local_record(
        title=("Attention Is All You Need", 0.8),
        authors=(["Ashish Vaswani"], 0.6),
        year=(2017, 0.6),
        venue=("NIPS 2017", 0.65),
        doi=("10.65215/ysbyhc05", 0.6),
    )

    resolve(record)

    assert record.year == 2017
    assert record.venue == "NIPS 2017"
    assert record.title == "Attention Is All You Need"
    assert record.warnings == [
        "local year 2017 disagrees with the 2025 preprint record"
    ]
    assert record.sources[0].query == "doi:10.65215/ysbyhc05"


def test_a_record_years_away_may_identify_the_paper_but_not_its_venue(
    online: Online,
) -> None:
    """A 2025 book chapter that merely shares the title keeps nothing else.

    The recorded answer for the plain title is a book chapter five words long,
    scored 0.61 against the 2017 paper: barely a match, and years away from the
    year the PDF prints. It may say which paper this is; its year, its venue and
    its DOI belong to that other edition.
    """
    online.file("api.crossref.org/works?", "crossref_search_attention_plain.json")
    online.text(
        "export.arxiv.org/api/query",
        "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'/>",
    )
    online.json("api.openalex.org/works?", {"results": []})
    online.json("api.semanticscholar.org", {"data": []})
    record = local_record(
        title=("Attention Is All You Need", 0.8),
        authors=(["Ashish Vaswani"], 0.6),
        year=(2017, 0.6),
        venue=("NIPS 2017", 0.65),
    )

    resolve(record)

    assert record.venue == "NIPS 2017"
    assert record.doi is None
    assert record.url is None
    assert record.year == 2017
    assert any(
        "local year 2017 disagrees with the 2025 record" in warning
        for warning in record.warnings
    )


def test_a_published_record_wins_over_a_mirror_at_the_same_score(
    online: Online,
) -> None:
    """Built from the recorded mirror: the same paper, posted and published.

    Crossref holds no published record of the 2017 paper, so the pair is the
    recorded mirror plus a copy of it declared a proceedings article. Both are
    dated 2017 - the year the PDF prints - so the year guard stays out of the
    way and only the preference between a posting and a published record
    decides which one contributes.
    """
    mirror = json.loads(recorded("crossref_doi_10.65215_ysbyhc05.json"))["message"]
    same_year = {**mirror, "issued": {"date-parts": [[2017]]}}
    posted = {**same_year, "type": "posted-content", "container-title": []}
    published = {
        **same_year,
        "type": "proceedings-article",
        "container-title": ["Advances in Neural Information Processing Systems"],
    }
    online.json(
        "api.crossref.org/works?query.bibliographic",
        {"status": "ok", "message": {"items": [posted, published]}},
    )
    record = local_record(
        title=("Attention Is All You Need", 0.8),
        authors=(["Ashish Vaswani"], 0.6),
        year=(2017, 0.6),
    )

    resolve(record)

    assert record.venue == "Advances in Neural Information Processing Systems"
    assert record.doi == "10.65215/ysbyhc05"
    assert record.year == 2017
    assert record.warnings == []


def test_an_older_file_version_keeps_the_first_version_year_and_says_so(
    online: Online,
) -> None:
    """arXiv answers with the latest version; the file names an older one."""
    online.file(
        "export.arxiv.org/api/query?id_list=2310.16789", "arxiv_id_2310.16789.xml"
    )
    older = local_record(
        arxiv_id=("2310.16789", 0.75),
        arxiv_version=("v2", 0.75),
    )

    resolve(older)

    assert older.arxiv_version == "v3"  # what arXiv now reports as the latest
    assert older.year == 2023  # the first version's year, not the v3 year
    assert older.warnings == [
        "the file is version v2 but arXiv reports v3 as the latest; "
        "the year is the first version's"
    ]


def test_the_latest_file_version_takes_the_year_of_that_version(
    online: Online,
) -> None:
    online.file(
        "export.arxiv.org/api/query?id_list=2310.16789", "arxiv_id_2310.16789.xml"
    )
    latest = local_record(
        arxiv_id=("2310.16789", 0.75),
        arxiv_version=("v3", 0.75),
    )

    resolve(latest)

    assert latest.year == 2024  # <updated>, the version the file holds
    assert latest.warnings == []


# -- nothing to do, and failures -------------------------------------------


def test_nothing_to_look_up_records_one_skipped_attempt(online: Online) -> None:
    record = PaperRecord(path="paper.pdf")

    resolve(record)

    assert len(record.sources) == 1
    assert record.sources[0].source == "online"
    assert record.sources[0].status == SOURCE_SKIPPED
    assert record.sources[0].error == "nothing to look up"
    assert online.urls == []


def test_a_junk_identifier_is_not_sent_to_anyone(online: Online) -> None:
    """A DOI-shaped string that is not a DOI never reaches a service."""
    online.file("api.crossref.org/works?", "crossref_search_membership_inference.json")
    record = local_record(doi=("doi: not-a-doi", 0.5), title=(MEMBERSHIP_TITLE, 0.8))

    resolve(record)

    assert all("not-a-doi" not in url for url in online.urls)
    assert sources(record) == [("crossref", SOURCE_OK)]
    # The title search then corrected the DOI the file had got wrong.
    assert record.doi == "10.1109/wacv57701.2024.00479"


def test_a_timeout_is_recorded_and_the_next_provider_still_answers(
    online: Online,
) -> None:
    online.fail("api.crossref.org/works/", TimeoutError("timed out"))
    online.file(
        "api.openalex.org/works/doi:", "openalex_doi_10.1038_s41467-024-53632-4.json"
    )
    record = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))

    result = resolve(record)

    assert result is record
    assert sources(record) == [
        ("crossref", SOURCE_ERROR),
        ("openalex", SOURCE_OK),
    ]
    assert record.sources[0].error is not None
    assert "cannot reach" in record.sources[0].error
    assert "timed out" in record.sources[0].error
    assert record.sources[0].query == "doi:10.1038/s41467-024-53632-4"
    assert (record.title or "").startswith("TamGen")
    assert len(online.urls) == http.RETRIES + 2  # two retries, then OpenAlex


def test_a_forbidden_response_a_500_and_a_broken_body_are_all_recorded(
    online: Online,
) -> None:
    online.json("api.crossref.org/works/", {"status": "ok"}, status=403)
    online.json("api.openalex.org/works/doi:", {}, status=500)
    online.broken("api.datacite.org/dois/")
    online.file(
        "api.semanticscholar.org",
        "semanticscholar_doi_10.1109_wacv57701.2024.00479.json",
    )
    record = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))

    resolve(record)

    assert sources(record)[:3] == [
        ("crossref", SOURCE_ERROR),
        ("openalex", SOURCE_ERROR),
        ("datacite", SOURCE_ERROR),
    ]
    assert "HTTP 403" in (record.sources[0].error or "")
    assert "HTTP 500" in (record.sources[1].error or "")
    assert "malformed JSON" in (record.sources[2].error or "")
    # The fourth provider still gets its turn, and the budget holds.
    assert sources(record)[3] == ("semanticscholar", SOURCE_OK)
    assert len(online.urls) == 6
    assert record.title == MEMBERSHIP_TITLE


def test_a_doi_the_file_only_guessed_at_does_not_win_over_the_search(
    online: Online,
) -> None:
    """Every DOI lookup fails; the title search still identifies the paper."""
    online.json("api.crossref.org/works/", {"status": "ok"}, status=404)
    online.json("api.openalex.org/works/doi:", {"error": "no"}, status=404)
    online.json("api.datacite.org/dois/", {"errors": []}, status=404)
    online.json(
        "api.semanticscholar.org/graph/v1/paper/DOI:", {"error": "no"}, status=404
    )
    online.file("api.crossref.org/works?", "crossref_search_membership_inference.json")
    record = local_record(
        doi=("10.1109/wacv57701.2024.99999", 0.5), title=(MEMBERSHIP_TITLE, 0.8)
    )

    resolve(record)

    assert record.doi == "10.1109/wacv57701.2024.00479"
    assert record.provenance["doi"].source == "crossref"
    assert sources(record) == [
        ("crossref", SOURCE_NOT_FOUND),
        ("openalex", SOURCE_NOT_FOUND),
        ("datacite", SOURCE_NOT_FOUND),
        ("semanticscholar", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_OK),
    ]


def test_the_budget_is_six_requests_and_later_steps_are_skipped(
    online: Online,
) -> None:
    online.json("api.crossref.org/works/", {}, status=404)
    online.json("api.openalex.org/works/doi:", {}, status=404)
    online.json("api.datacite.org/dois/", {}, status=404)
    online.json("api.semanticscholar.org/graph/v1/paper/DOI:", {}, status=404)
    online.text(
        "export.arxiv.org/api/query?id_list=",
        "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'/>",
    )
    online.json("api.crossref.org/works?", {"message": {"items": []}})
    online.json("api.openalex.org/works?", {"results": []})
    record = local_record(
        doi=("10.1038/s41467-024-53632-4", 0.8),
        arxiv_id=("2306.12983", 0.8),
        title=(MEMBERSHIP_TITLE, 0.8),
    )

    resolve(record)

    assert len(online.urls) == 6
    assert sources(record) == [
        ("crossref", SOURCE_NOT_FOUND),
        ("openalex", SOURCE_NOT_FOUND),
        ("datacite", SOURCE_NOT_FOUND),
        ("semanticscholar", SOURCE_NOT_FOUND),
        ("arxiv", SOURCE_NOT_FOUND),
        ("crossref", SOURCE_NOT_FOUND),
    ]
    assert record.title == MEMBERSHIP_TITLE
    assert record.provenance["title"].source == "pdf-first-page"


def test_an_unexpected_schema_is_an_attempt_not_a_crash(online: Online) -> None:
    online.json("api.crossref.org/works/", {"message": "not an object"})
    online.json(
        "api.openalex.org/works/doi:",
        {"title": {"nested": "object"}, "doi": "not a doi"},
    )
    online.json("api.datacite.org/dois/", {"data": []})
    online.json("api.semanticscholar.org", {"data": "not a paper"})
    record = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))

    resolve(record)

    assert sources(record) == [
        ("crossref", SOURCE_NOT_FOUND),
        ("openalex", SOURCE_NOT_FOUND),
        ("datacite", SOURCE_NOT_FOUND),
        ("semanticscholar", SOURCE_NOT_FOUND),
    ]
    assert record.title is None


def test_resolve_reports_progress_when_asked(
    online: Online, capsys: pytest.CaptureFixture[str]
) -> None:
    online.json("api.crossref.org/works/", {"status": "ok"}, status=404)
    record = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))

    resolve(record, verbose=True)

    stderr = capsys.readouterr().err
    assert "apr: crossref: not-found" in stderr


# -- caching and pacing -----------------------------------------------------


def test_the_second_run_with_a_cache_costs_nothing(
    online: Online, tmp_path: Path
) -> None:
    online.file(
        "api.crossref.org/works/10.1038/s41467",
        "crossref_doi_10.1038_s41467-024-53632-4.json",
    )
    first = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))
    resolve(first, cache_dir=tmp_path)
    opened = len(online.urls)

    second = local_record(doi=("10.1038/s41467-024-53632-4", 0.8))
    resolve(second, cache_dir=tmp_path)

    assert len(online.urls) == opened
    assert second.title == first.title


def test_without_a_cache_dir_the_second_run_asks_again(online: Online) -> None:
    online.file(
        "api.crossref.org/works/10.1038/s41467",
        "crossref_doi_10.1038_s41467-024-53632-4.json",
    )
    for _ in range(2):
        resolve(local_record(doi=("10.1038/s41467-024-53632-4", 0.8)), cache_dir=None)

    assert len(online.urls) == 2


def test_two_requests_to_one_host_are_paced(online: Online) -> None:
    online.json("api.crossref.org/works/", {"status": "ok"}, status=404)
    online.json("api.openalex.org/works/doi:", {"error": "no"}, status=404)
    online.json("api.datacite.org/dois/", {"errors": []}, status=404)
    online.json(
        "api.semanticscholar.org/graph/v1/paper/DOI:", {"error": "no"}, status=404
    )
    online.file("api.crossref.org/works?", "crossref_search_membership_inference.json")
    record = local_record(
        doi=("10.1109/wacv57701.2024.99999", 0.5), title=(MEMBERSHIP_TITLE, 0.8)
    )

    resolve(record)

    hosts = [online.host(url) for url in online.urls]
    assert hosts.count("api.crossref.org") == 2
    assert hosts[:4] == [
        "api.crossref.org",
        "api.openalex.org",
        "api.datacite.org",
        "api.semanticscholar.org",
    ]
    # Only the repeat visit to Crossref waited; the other hosts were asked once.
    assert online.slept == [pytest.approx(http.MIN_HOST_INTERVAL, abs=0.1)]
