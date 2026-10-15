"""Each provider parses its recorded response; no socket is involved.

``apr.providers.http`` is replaced with a recorded body, so these tests pin the
parsing and the query shape of every provider, not the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from apr.match import Candidate
from apr.providers import arxiv, crossref, datacite, http, openalex, semanticscholar

RECORDED = Path(__file__).parent / "recorded"


def recorded(name: str) -> str:
    return (RECORDED / name).read_text("utf-8")


class Server:
    """A recorded body in place of the HTTP layer's socket."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.text = ""
        self.failure: int | None = None

    def serve(self, name: str) -> None:
        self.text = recorded(name)

    def body(self, raw: str) -> None:
        self.text = raw

    def fail(self, status: int) -> None:
        self.failure = status

    def options(self) -> http.HttpOptions:
        return http.HttpOptions()

    @property
    def last_url(self) -> str:
        return self.calls[-1]

    def query(self) -> dict[str, list[str]]:
        """The decoded query of the last request, for checking its shape."""
        return parse_qs(urlsplit(self.last_url).query)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Server:
    server = Server()

    def fail_if_asked() -> None:
        if server.failure is not None:
            raise http.HttpError(
                f"HTTP {server.failure} from {server.last_url}",
                url=server.last_url,
                status=server.failure,
            )

    def get_text(url: str, *, options: http.HttpOptions | None = None) -> str:
        server.calls.append(url)
        fail_if_asked()
        return server.text

    def get_json(url: str, *, options: http.HttpOptions | None = None) -> object:
        server.calls.append(url)
        fail_if_asked()
        return json.loads(server.text)

    monkeypatch.setattr(http, "get_text", get_text)
    monkeypatch.setattr(http, "get_json", get_json)
    return server


# -- arXiv -----------------------------------------------------------------


def test_arxiv_by_id_parses_the_recorded_feed(server: Server) -> None:
    server.serve("arxiv_id_2306.12983.xml")

    candidate = arxiv.by_id("2306.12983", options=server.options())

    assert candidate is not None
    assert candidate.title == (
        "Towards More Realistic Membership Inference Attacks on Large Diffusion Models"
    )
    assert candidate.authors[:2] == ["Jan Dubiński", "Antoni Kowalczuk"]
    assert candidate.authors[-1] == "Paweł Morawiecki"
    assert candidate.year == 2023
    assert candidate.venue == "arXiv"
    assert candidate.type == "preprint"
    assert candidate.doi is None
    assert (candidate.arxiv_id, candidate.arxiv_version) == ("2306.12983", "v2")
    assert candidate.url == "https://arxiv.org/abs/2306.12983v2"
    assert candidate.abstract is not None
    assert candidate.abstract.startswith("Generative diffusion models")
    assert candidate.note is None
    assert server.calls == [
        "https://export.arxiv.org/api/query?id_list=2306.12983&max_results=1"
    ]


def test_arxiv_by_id_reads_the_published_doi_and_journal_reference(
    server: Server,
) -> None:
    server.serve("arxiv_id_2105.04565.xml")

    candidate = arxiv.by_id("2105.04565", options=server.options())

    assert candidate is not None
    assert candidate.doi == "10.1103/physrevd.104.095029"
    assert candidate.venue == "Phys. Rev. D 104 (2021) 095029"
    assert candidate.year == 2021
    assert candidate.url == "https://arxiv.org/abs/2105.04565v3"
    # A journal reference means the entry describes a published paper.
    assert candidate.type == "article"


def test_arxiv_by_id_follows_the_version_the_file_holds(server: Server) -> None:
    """The API answers with the latest version; the file may hold an older one."""
    server.serve("arxiv_id_2310.16789.xml")

    latest = arxiv.by_id("2310.16789", reported_version="v3", options=server.options())
    older = arxiv.by_id("2310.16789", reported_version="v2", options=server.options())
    unknown = arxiv.by_id("2310.16789", options=server.options())

    assert latest is not None and older is not None and unknown is not None
    assert latest.year == 2024  # <updated>, the version in the file
    assert latest.note is None
    assert older.year == 2023  # <published>, the first version's year
    assert older.note is not None
    assert "version v2" in older.note and "v3" in older.note
    assert unknown.year == 2023
    assert unknown.note is None


def test_arxiv_by_id_returns_nothing_for_an_empty_feed(server: Server) -> None:
    server.body(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>ArXiv Query</title></feed>'
    )
    assert arxiv.by_id("2306.99999", options=server.options()) is None


def test_arxiv_by_id_returns_nothing_for_an_error_entry(server: Server) -> None:
    """arXiv answers a malformed id with a feed whose only entry is an error."""
    server.body(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_x</id>"
        "<title>Error</title><summary>incorrect id format</summary></entry></feed>"
    )
    assert arxiv.by_id("x", options=server.options()) is None


def test_arxiv_search_asks_for_the_title_and_the_first_author(server: Server) -> None:
    server.serve("arxiv_search_attention.xml")

    candidates = arxiv.search(
        "Attention is all you need", ["Ashish Vaswani"], options=server.options()
    )

    assert server.query() == {
        "search_query": ['ti:"Attention is all you need" AND au:"Vaswani"'],
        "max_results": ["5"],
    }
    assert len(candidates) == 1
    assert candidates[0].title == "Attention Is All You Need"
    assert candidates[0].authors[0] == "Ashish Vaswani"
    assert (candidates[0].arxiv_id, candidates[0].arxiv_version) == (
        "1706.03762",
        "v7",
    )


def test_arxiv_search_without_an_author_asks_for_the_title_alone(
    server: Server,
) -> None:
    server.serve("arxiv_search_attention.xml")

    arxiv.search("Attention is all you need", options=server.options())

    assert server.query()["search_query"] == ['ti:"Attention is all you need"']


def test_arxiv_search_terms_cannot_break_out_of_the_query(server: Server) -> None:
    server.serve("arxiv_search_attention.xml")

    arxiv.search('A "quoted" title', ["O'Brien"], options=server.options())

    assert server.query()["search_query"] == ['ti:"A quoted title" AND au:"O\'Brien"']


# -- Crossref ---------------------------------------------------------------


def test_crossref_by_doi_parses_the_recorded_work(server: Server) -> None:
    server.serve("crossref_doi_10.1038_s41467-024-53632-4.json")

    candidate = crossref.by_doi(
        "10.1038/s41467-024-53632-4", mailto="me@example.com", options=server.options()
    )

    assert candidate is not None
    assert candidate.title == (
        "TamGen: drug design with target-aware molecule generation through a "
        "chemical language model"
    )
    assert candidate.authors[0] == "Kehan Wu"
    assert candidate.year == 2024
    assert candidate.venue == "Nature Communications"
    assert candidate.doi == "10.1038/s41467-024-53632-4"
    assert candidate.url == "https://doi.org/10.1038/s41467-024-53632-4"
    assert candidate.abstract is not None
    assert candidate.abstract.startswith("Generative drug design")
    assert "<" not in candidate.abstract
    assert server.query() == {"mailto": ["me@example.com"]}


def test_crossref_by_doi_returns_nothing_for_an_unknown_doi(server: Server) -> None:
    server.fail(404)

    assert crossref.by_doi("10.5281/zenodo.999", options=server.options()) is None


def test_crossref_search_asks_for_five_bibliographic_rows(server: Server) -> None:
    """One query string, five rows, and nothing else narrowing it.

    This replaced the old contract, which sent the first author alongside the
    title as a separate ``query.author`` parameter. Since the bibliographic
    search started losing records Crossref holds, the plain title goes first
    and the author is only added if that finds nothing acceptable.
    """
    server.serve("crossref_search_membership_inference_plain.json")

    candidates = crossref.search(
        "Towards More Realistic Membership Inference Attacks on Large Diffusion Models",
        mailto="me@example.com",
        options=server.options(),
    )

    assert server.query() == {
        "query.bibliographic": [
            "Towards More Realistic Membership Inference Attacks on Large Diffusion "
            "Models"
        ],
        "rows": ["5"],
        "mailto": ["me@example.com"],
    }
    assert "filter" not in server.last_url  # no date or type filter, ever
    assert "query.author" not in server.last_url
    assert candidates[0].doi == "10.1109/wacv57701.2024.00479"
    assert candidates[0].type == "proceedings-article"
    assert candidates[0].title == (
        "Towards More Realistic Membership Inference Attacks on Large Diffusion Models"
    )
    assert candidates[0].authors[0] == "Jan Dubiński"
    assert candidates[0].venue == (
        "2024 IEEE/CVF Winter Conference on Applications of Computer Vision (WACV)"
    )
    assert candidates[0].abstract is None


def test_crossref_search_url_carries_no_filter_and_no_author(
    server: Server,
) -> None:
    """The query is the caller's text; nothing narrows it behind their back.

    The old contract put the first author in ``query.author``; the new one
    carries a plain title and leaves the narrowing to the caller's retries.
    """
    url = crossref.bibliography_url("a plain title", mailto="me@example.com")

    assert url == (
        "https://api.crossref.org/works?query.bibliographic=a+plain+title"
        "&rows=5&mailto=me%40example.com"
    )
    assert "filter" not in url
    assert "query.author" not in url


def test_crossref_ignores_a_body_without_a_message(server: Server) -> None:
    server.body('{"status": "ok"}')

    assert crossref.by_doi("10.1/x", options=server.options()) is None
    assert crossref.search("anything", options=server.options()) == []


def test_crossref_puts_whole_names_in_one_order_too(server: Server) -> None:
    """A work whose authors arrive as one ``name`` string, family-first."""
    server.body(
        json.dumps(
            {
                "status": "ok",
                "message": {
                    "DOI": "10.1109/wacv57701.2024.00479",
                    "title": ["A title"],
                    "author": [
                        {"name": "Dubiński, Jan"},
                        {"name": "IEEE Computer Society"},
                        {"given": "Antoni", "family": "Kowalczuk"},
                    ],
                },
            }
        )
    )

    candidate = crossref.by_doi("10.1/x", options=server.options())

    assert candidate is not None
    assert candidate.authors == [
        "Jan Dubiński",
        "IEEE Computer Society",
        "Antoni Kowalczuk",
    ]


# -- OpenAlex ---------------------------------------------------------------


def test_openalex_by_doi_parses_the_recorded_work(server: Server) -> None:
    server.serve("openalex_doi_10.1038_s41467-024-53632-4.json")

    candidate = openalex.by_doi(
        "10.1038/s41467-024-53632-4", mailto="me@example.com", options=server.options()
    )

    assert candidate is not None
    assert candidate.title == (
        "TamGen: drug design with target-aware molecule generation through a "
        "chemical language model"
    )
    assert candidate.authors[0] == "Kehan Wu"
    assert candidate.year == 2024
    assert candidate.venue == "Nature Communications"
    assert candidate.doi == "10.1038/s41467-024-53632-4"
    assert candidate.url == "https://doi.org/10.1038/s41467-024-53632-4"
    assert candidate.abstract == (
        "Abstract Generative drug design facilitates the creation of compounds "
        "effective against pathogenic target proteins."
    )
    assert server.last_url.startswith("https://api.openalex.org/works/doi:")
    assert server.query() == {"mailto": ["me@example.com"]}
    assert candidate.type == "article"


def test_openalex_puts_mixed_author_names_in_one_order(server: Server) -> None:
    """OpenAlex prints 'Zeng, Cheng' and 'Jirui Jin' in the same author list."""
    server.serve("openalex_doi_10.48550_arxiv.2505.21469.json")

    candidate = openalex.by_doi("10.48550/arxiv.2505.21469", options=server.options())

    assert candidate is not None
    assert candidate.authors == [
        "Cheng Zeng",
        "Jirui Jin",
        "Connor Ambrose",
        "George Karypis",
        "Mark K. Transtrum",
        "Ellad B. Tadmor",
    ]
    assert candidate.type == "preprint"
    assert candidate.venue == "arXiv"


def test_openalex_search_returns_every_candidate_it_can_parse(server: Server) -> None:
    server.serve("openalex_search_attention.json")

    candidates = openalex.search(
        "Attention is all you need", ["Ashish Vaswani"], options=server.options()
    )

    assert server.query() == {
        "search": ["Attention is all you need"],
        "per-page": ["5"],
    }
    assert len(candidates) == 5
    assert candidates[0].title == "Attention Is All You Need"
    assert candidates[0].year == 2025
    assert candidates[0].doi == "10.65215/2q58a426"
    assert "Attention Is All You Need In Speech Separation" in [
        candidate.title for candidate in candidates
    ]
    by_title = {candidate.title: candidate for candidate in candidates}
    assert by_title["Attention Is All You Need In Speech Separation"].venue == (
        "IEEE International Conference on Acoustics Speech and Signal Processing"
    )
    assert by_title["Attention Is All You Need"].venue is None


def test_openalex_survives_a_body_of_the_wrong_shape(server: Server) -> None:
    server.body('{"results": {"not": "a list"}}')

    assert openalex.search("anything", options=server.options()) == []
    assert openalex.by_doi("10.1/x", options=server.options()) is None


# -- Semantic Scholar -------------------------------------------------------


def test_semanticscholar_by_doi_reads_the_external_identifiers(
    server: Server,
) -> None:
    server.serve("semanticscholar_doi_10.1109_wacv57701.2024.00479.json")

    candidate = semanticscholar.by_doi(
        "10.1109/WACV57701.2024.00479", options=server.options()
    )

    assert candidate is not None
    assert candidate.title == (
        "Towards More Realistic Membership Inference Attacks on Large Diffusion Models"
    )
    # Semantic Scholar dates the arXiv preprint, not the WACV proceedings.
    assert candidate.year == 2023
    assert candidate.doi == "10.1109/wacv57701.2024.00479"
    assert (candidate.arxiv_id, candidate.arxiv_version) == ("2306.12983", None)
    assert candidate.url == "https://arxiv.org/abs/2306.12983"
    assert candidate.venue == (
        "IEEE Workshop/Winter Conference on Applications of Computer Vision"
    )
    assert candidate.abstract is not None


def test_semanticscholar_match_returns_the_recorded_best_match(server: Server) -> None:
    server.serve("semanticscholar_match_attention.json")
    candidates = semanticscholar.match(
        "Attention is all you need", options=server.options()
    )

    assert server.query()["query"] == ["Attention is all you need"]
    assert "fields" in server.query()
    assert len(candidates) == 1
    assert candidates[0].title == "Attention is All you Need"
    assert candidates[0].year == 2017
    assert candidates[0].authors[0] == "Ashish Vaswani"
    assert candidates[0].doi is None
    assert candidates[0].arxiv_id == "1706.03762"
    assert candidates[0].venue == "Neural Information Processing Systems"
    assert candidates[0].abstract is not None


def test_semanticscholar_puts_whole_names_in_one_order_too(server: Server) -> None:
    server.body(
        json.dumps(
            {
                "data": [
                    {
                        "title": "A title",
                        "authors": [
                            {"authorId": "1", "name": "Vaswani, Ashish"},
                            {"authorId": "2", "name": "Noam Shazeer"},
                        ],
                        "publicationTypes": ["JournalArticle"],
                    }
                ]
            }
        )
    )

    candidates = semanticscholar.match("a title", options=server.options())

    assert candidates[0].authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert candidates[0].type == "journalarticle"
    assert candidates[0].is_preprint is False


def test_semanticscholar_reports_a_missing_match_as_nothing(server: Server) -> None:
    server.fail(404)

    assert semanticscholar.match("nothing like it", options=server.options()) == []
    assert semanticscholar.by_doi("10.1/x", options=server.options()) is None


def test_semanticscholar_survives_a_body_of_the_wrong_shape(server: Server) -> None:
    server.body('{"data": "no papers here"}')

    assert semanticscholar.match("anything", options=server.options()) == []


# -- DataCite ---------------------------------------------------------------


def test_datacite_by_doi_inverts_family_given_names(server: Server) -> None:
    server.serve("datacite_doi_10.5281_zenodo.18289746.json")

    candidate = datacite.by_doi("10.5281/zenodo.18289746", options=server.options())

    assert candidate is not None
    assert candidate.title == "Attention is all you need"
    assert candidate.authors[0] == "Beat Döbeli Honegger"
    assert candidate.year == 2026
    assert candidate.doi == "10.5281/zenodo.18289746"
    assert candidate.url == "https://doi.org/10.5281/zenodo.18289746"
    assert candidate.abstract is not None
    assert candidate.abstract.startswith("«Attention is all you need»") or (
        candidate.abstract.startswith("\u00abAttention is all you need\u00bb")
    )
    # DataCite's publisher is the depositor, not a venue.
    assert candidate.venue is None
    assert server.calls == ["https://api.datacite.org/dois/10.5281/zenodo.18289746"]


def test_datacite_by_doi_returns_nothing_when_the_doi_is_not_registered(
    server: Server,
) -> None:
    server.fail(404)

    assert (
        datacite.by_doi("10.1038/s41467-024-53632-4", options=server.options()) is None
    )


# -- shared failure behaviour ----------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda options: crossref.by_doi("10.1/x", options=options), id="crossref"
        ),
        pytest.param(
            lambda options: openalex.by_doi("10.1/x", options=options), id="openalex"
        ),
        pytest.param(
            lambda options: datacite.by_doi("10.1/x", options=options), id="datacite"
        ),
        pytest.param(
            lambda options: semanticscholar.by_doi("10.1/x", options=options),
            id="semanticscholar",
        ),
        pytest.param(
            lambda options: arxiv.by_id("2306.12983", options=options), id="arxiv"
        ),
    ],
)
def test_a_server_error_is_raised_for_resolve_to_record(
    server: Server, call: Callable[[http.HttpOptions], Candidate | None]
) -> None:
    server.fail(503)

    with pytest.raises(http.HttpError) as raised:
        call(server.options())

    assert raised.value.status == 503
    assert not raised.value.not_found
