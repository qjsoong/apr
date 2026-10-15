"""Tests for apr.match: normalisation and scoring, no I/O involved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apr.match import (
    AUTHOR_WEIGHT,
    COMPARABLE_SCORE,
    CONFIDENT_MATCH,
    CROSS_CHECK_THRESHOLD,
    MATCH_THRESHOLD,
    TITLE_WEIGHT,
    YEAR_WEIGHT,
    Candidate,
    abstract_from_inverted_index,
    author_score,
    best_candidate,
    canonical_url,
    cross_check,
    invert_name,
    normalise_arxiv_version,
    normalise_doi,
    normalise_title,
    parse_arxiv_id,
    preference,
    score_candidate,
    strip_markup,
    surname,
    title_score,
    year_score,
)

RECORDED = Path(__file__).parent / "recorded"


def recorded(name: str) -> str:
    return (RECORDED / name).read_text("utf-8")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1038/s41467-024-53632-4", "10.1038/s41467-024-53632-4"),
        ("https://doi.org/10.1038/S41467-024-53632-4", "10.1038/s41467-024-53632-4"),
        (
            "http://dx.doi.org/10.1103/PhysRevD.104.095029",
            "10.1103/physrevd.104.095029",
        ),
        ("doi:10.1103/PhysRevD.104.095029", "10.1103/physrevd.104.095029"),
        ("DOI: 10.1145/3290605.3300233", "10.1145/3290605.3300233"),
        ("urn:doi:10.1000/xyz123", "10.1000/xyz123"),
        ("  10.1109/WACV57701.2024.00479  ", "10.1109/wacv57701.2024.00479"),
        ("10.1038/s41467-024-53632-4.", "10.1038/s41467-024-53632-4"),
        ("10.1038/s41467-024-53632-4),", "10.1038/s41467-024-53632-4"),
        ("arXiv:2306.12983", None),
        ("10.1038", None),
        ("not a doi at all", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_normalise_doi_strips_the_url_form_prefix_and_trailing_punctuation(
    value: object, expected: str | None
) -> None:
    assert normalise_doi(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2306.12983", ("2306.12983", None)),
        ("2306.12983v2", ("2306.12983", "v2")),
        ("arXiv:2306.12983v2", ("2306.12983", "v2")),
        ("https://arxiv.org/abs/2610.01939v2", ("2610.01939", "v2")),
        ("https://arxiv.org/pdf/2306.12983v2.pdf", ("2306.12983", "v2")),
        ("cs/0701001v1", ("cs/0701001", "v1")),
        ("cond-mat.str-el/0701001", ("cond-mat.str-el/0701001", None)),
        ("1706.03762", ("1706.03762", None)),
        ("https://doi.org/10.1103/PhysRevD.104.095029", None),
        ("not an id", None),
        (None, None),
    ],
)
def test_parse_arxiv_id(value: object, expected: tuple[str, str | None] | None) -> None:
    assert parse_arxiv_id(value) == expected


def test_normalise_arxiv_version_accepts_the_forms_extraction_produces() -> None:
    assert normalise_arxiv_version("v3") == "v3"
    assert normalise_arxiv_version("V3") == "v3"
    assert normalise_arxiv_version(" 3 ") == "v3"
    assert normalise_arxiv_version("v") is None
    assert normalise_arxiv_version("latest") is None
    assert normalise_arxiv_version(None) is None


def test_canonical_url_prefers_arxiv_and_keeps_the_version() -> None:
    assert (
        canonical_url(doi=None, arxiv_id="2610.01939", version="v2")
        == "https://arxiv.org/abs/2610.01939v2"
    )
    assert canonical_url(doi="10.1038/s41467-024-53632-4") == (
        "https://doi.org/10.1038/s41467-024-53632-4"
    )
    assert canonical_url() is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Attention Is All You Need", "attention is all you need"),
        ("  Attention   Is\nAll You Need  ", "attention is all you need"),
        ("The State-of-the-Art in Genera-\n tion", "state of the art in generation"),
        ("Dubiński", "dubinski"),
        ("Fewer Tokens, Better Action: …", "fewer tokens better action"),
        ("A Study of the Ribosome", "a study of the ribosome"),
        (None, ""),
    ],
)
def test_normalise_title_folds_case_accents_punctuation_and_layout(
    value: object, expected: str
) -> None:
    assert normalise_title(value) == expected


def test_title_score_is_one_for_the_same_title_and_low_for_another() -> None:
    assert title_score("Attention Is All You Need", "attention is all you need") == 1.0
    assert title_score("Attention is all you need", "a study of the ribosome") < 0.3
    assert title_score("", "Attention is all you need") == 0.0


def test_title_score_keeps_a_near_miss_below_the_confident_bar() -> None:
    """A near miss that reuses every word but means another paper."""
    local = "Attention is all you need"
    near = "Attention Is All You Need In Speech Separation"
    unrelated = (
        "Towards More Realistic Membership Inference Attacks on Diffusion Models"
    )
    assert 0.6 <= title_score(local, "Is Attention All You Need?") < 1.0
    assert 0.6 <= title_score(local, near) < 0.85
    assert title_score(local, unrelated) < 0.3

    # With the local author list known, the near miss no longer reaches the gate.
    mismatch = score_candidate(
        Candidate(title=near, authors=["Yi Luo", "Nima Mesgarani"]),
        title=local,
        authors=["Ashish Vaswani"],
    )
    assert mismatch < 0.6


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # OpenAlex, Crossref and DataCite all mix these two orders in one list.
        ("Zeng, Cheng", "Cheng Zeng"),
        ("Ambrose, Connor", "Connor Ambrose"),
        ("Dubiński, Jan", "Jan Dubiński"),
        ("Döbeli Honegger, Beat", "Beat Döbeli Honegger"),
        ("van der Berg, Jan", "Jan van der Berg"),
        ("Jirui Jin", "Jirui Jin"),
        ("Ashish Vaswani", "Ashish Vaswani"),
        # A suffix is not a given name, and neither is an empty head.
        ("Smith, John, Jr.", "Smith, John, Jr."),
        ("Smith, John, Sr.", "Smith, John, Sr."),
        ("Smith, John, III", "Smith, John, III"),
        (", Jan", ", Jan"),
        ("Smith, John II", "John II Smith"),
        (None, ""),
    ],
)
def test_invert_name_turns_around_only_a_given_name(
    value: object, expected: str
) -> None:
    assert invert_name(value) == expected


def test_surname_is_read_as_printed_for_a_query() -> None:
    """Unfolded on purpose: Crossref matches author queries with diacritics."""
    assert surname("Jan Dubiński") == "Dubiński"
    assert surname("Dubiński, Jan") == "Dubiński"
    assert surname("Ashish Vaswani") == "Vaswani"
    assert surname("") == ""


def test_author_score_matches_surname_and_initial_across_spellings() -> None:
    assert author_score(["Jan Dubiński"], ["Jan Dubinski"]) == 1.0
    assert author_score(["J. Dubiński"], ["Jan Dubiński"]) == 1.0
    assert author_score(["Dubiński, Jan"], ["Jan Dubiński"]) == 1.0
    assert author_score(["Aidan N. Gomez"], ["Aidan", "N.Gomez"]) == 0.0
    assert author_score(["Aidan N. Gomez"], ["Aidan N.Gomez"]) == 1.0
    assert author_score(["Jan Dubiński"], ["Ashish Vaswani"]) == 0.0
    assert author_score(["Jan Dubiński"], []) == 0.0


def test_author_score_compares_only_the_first_authors() -> None:
    local = ["Jan Dubiński", "Antoni Kowalczuk", "Stanisław Pawlak", "Tomasz Trzciński"]
    assert author_score(local, ["Jan Dubiński", "Antoni Kowalczuk"]) == pytest.approx(
        2 / 3
    )
    assert author_score([], ["Jan Dubiński"]) == 0.0


def test_year_score_decays_with_distance() -> None:
    assert year_score(2024, 2024) == 1.0
    assert year_score(2024, 2023) == pytest.approx(2 / 3)
    assert year_score(2024, 2022) == pytest.approx(1 / 3)
    assert year_score(2024, 2017) == 0.0
    assert year_score(None, 2024) == 0.0
    assert year_score(2024, None) == 0.0


def test_score_candidate_renormalises_over_the_components_both_sides_have() -> None:
    candidate = Candidate(
        title="Attention Is All You Need", authors=["Ashish Vaswani"], year=2017
    )
    title_only = score_candidate(candidate, title="Attention is all you need")
    assert title_only == 1.0
    assert title_only == (TITLE_WEIGHT * 1.0) / TITLE_WEIGHT

    with_author = score_candidate(
        candidate, title="Attention is all you need", authors=["A. Vaswani"]
    )
    assert with_author == 1.0

    disagreeing = score_candidate(
        candidate,
        title="Attention is all you need",
        authors=["Jan Dubiński"],
        year=2017,
    )
    expected = (TITLE_WEIGHT * 1.0 + AUTHOR_WEIGHT * 0.0 + YEAR_WEIGHT * 1.0) / (
        TITLE_WEIGHT + AUTHOR_WEIGHT + YEAR_WEIGHT
    )
    assert disagreeing == pytest.approx(expected)
    assert disagreeing < with_author


def test_score_candidate_of_a_recorded_near_miss_stays_below_the_gate() -> None:
    """Real Crossref search hits for the WACV paper, one of them a near miss."""
    items = json.loads(recorded("crossref_search_membership_inference.json"))[
        "message"
    ]["items"]
    local_title = "Towards More Realistic Membership Inference Attacks on Large Models"
    scores = [
        score_candidate(
            Candidate(
                title=(item.get("title") or [""])[0],
                authors=[
                    f"{author.get('given', '')} {author.get('family', '')}".strip()
                    for author in item.get("author", [])
                ],
                year=item.get("issued", {}).get("date-parts", [[None]])[0][0],
            ),
            title=local_title,
            authors=["Jan Dubiński"],
        )
        for item in items
    ]
    assert max(scores) >= 0.6
    assert sorted(scores)[-2] < 0.6


def test_strip_markup_removes_jats_tags_and_unescapes_entities() -> None:
    jats = (
        "<jats:title>Abstract</jats:title>"
        "<jats:p>We study <jats:italic>attention</jats:italic> &amp; recurrence."
        "</jats:p>"
    )
    assert strip_markup(jats) == "Abstract We study attention & recurrence."
    assert strip_markup(None) == ""


def test_strip_markup_on_a_real_crossref_abstract() -> None:
    work = json.loads(recorded("crossref_doi_10.1038_s41467-024-53632-4.json"))[
        "message"
    ]
    abstract = strip_markup(work["abstract"])
    assert abstract.startswith("Generative drug design facilitates the creation")
    assert "<" not in abstract
    assert "&" not in abstract


def test_cross_check_says_nothing_when_the_pdf_gave_no_title() -> None:
    """Nothing to compare is not a disagreement."""
    candidate = Candidate(title="Attention Is All You Need", authors=["Ashish Vaswani"])
    assert cross_check(candidate, title=None) is None
    assert cross_check(candidate, title="   ") is None
    assert cross_check(Candidate(title=None), title="Attention Is All You Need") is None


def test_cross_check_agrees_when_the_record_is_the_pdf_s_paper() -> None:
    candidate = Candidate(
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
    )

    agreement = cross_check(
        candidate,
        title="Attention is all you need",
        authors=["A. Vaswani", "N. Shazeer"],
    )

    assert agreement is not None
    assert agreement == 1.0
    assert agreement >= CROSS_CHECK_THRESHOLD


def test_cross_check_flags_a_record_that_is_another_paper() -> None:
    """A PDF that prints the TamGen title behind another paper's DOI."""
    work = json.loads(recorded("crossref_doi_10.1038_s41467-025-60763-9.json"))[
        "message"
    ]
    candidate = Candidate(
        title=work["title"][0],
        authors=[
            f"{author.get('given', '')} {author.get('family', '')}".strip()
            for author in work["author"]
        ],
    )
    local = (
        "TamGen: drug design with target-aware molecule generation through a "
        "chemical language model"
    )

    agreement = cross_check(candidate, title=local)

    assert agreement is not None
    assert agreement == title_score(local, candidate.title)
    assert agreement < CROSS_CHECK_THRESHOLD


def test_cross_check_is_capped_by_the_author_list() -> None:
    """The right title with a different first author is still a disagreement."""
    candidate = Candidate(
        title="Attention Is All You Need", authors=["Someone Else", "A. Nobody"]
    )

    assert (
        cross_check(
            candidate, title="Attention is all you need", authors=["Ashish Vaswani"]
        )
        == 0.0
    )


def test_the_two_gates_did_not_move() -> None:
    """The bars are the contract; scoring changes must not shift them.

    The year now carries more weight and a published record can beat a posting,
    neither of which may change when a candidate counts at all (0.60) or when a
    match is confident enough to stop looking (0.85). This test is here so the
    next weight change has to face the question.
    """
    assert MATCH_THRESHOLD == 0.60
    assert CONFIDENT_MATCH == 0.85
    assert MATCH_THRESHOLD < CONFIDENT_MATCH
    assert pytest.approx(1.0) == TITLE_WEIGHT + AUTHOR_WEIGHT + YEAR_WEIGHT


def test_a_perfect_title_years_away_cannot_look_confident() -> None:
    """Same title and authors, but the record is of another year: not a match.

    A mirror of the 2017 paper is issued in 2025; the year the PDF prints is
    part of the evidence, so that record cannot reach the confident bar.
    """
    candidate = Candidate(
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        year=2025,
    )
    score = score_candidate(
        candidate,
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        year=2017,
    )

    assert score == pytest.approx(TITLE_WEIGHT + AUTHOR_WEIGHT)
    assert score < CONFIDENT_MATCH
    assert score < 0.9
    # The same record with the right year is a perfect match.
    assert (
        score_candidate(
            candidate,
            title="Attention Is All You Need",
            authors=["Ashish Vaswani", "Noam Shazeer"],
            year=2025,
        )
        == 1.0
    )


def test_preference_prefers_a_published_record_and_a_doi() -> None:
    published_with_doi = Candidate(doi="10.1/x", type="proceedings-article")
    published_without = Candidate(type="journal-article")
    posted_with_doi = Candidate(doi="10.1/y", type="posted-content")
    posted_without = Candidate(type="preprint")

    assert preference(published_with_doi) > preference(published_without)
    assert preference(published_without) > preference(posted_with_doi)
    assert preference(posted_with_doi) > preference(posted_without)
    assert posted_with_doi.is_preprint and published_with_doi.is_preprint is False


def test_best_candidate_breaks_a_tie_towards_the_published_record() -> None:
    mirror = Candidate(
        title="Attention Is All You Need", doi="10.1/mirror", type="posted-content"
    )
    proceedings = Candidate(
        title="Attention Is All You Need",
        doi="10.1/proceedings",
        type="proceedings-article",
    )

    score, chosen = best_candidate([(1.0, mirror), (1.0, proceedings)])

    assert score == 1.0
    assert chosen is proceedings


def test_best_candidate_keeps_a_clearly_better_match() -> None:
    """Only near-equal scores are reordered."""
    better = Candidate(title="Attention Is All You Need", type="posted-content")
    worse = Candidate(
        title="Attention Is All You Need", doi="10.1/x", type="journal-article"
    )

    score, chosen = best_candidate([(1.0, better), (1.0 - COMPARABLE_SCORE * 2, worse)])

    assert score == 1.0
    assert chosen is better


def test_abstract_from_inverted_index_rebuilds_word_order() -> None:
    assert (
        abstract_from_inverted_index({"world": [1], "hello": [0], "again": [2]})
        == "hello world again"
    )
    assert abstract_from_inverted_index(None) is None
    assert abstract_from_inverted_index({}) is None
    assert abstract_from_inverted_index({"broken": "no positions"}) is None


def test_abstract_from_inverted_index_on_a_real_openalex_record() -> None:
    work = json.loads(recorded("openalex_doi_10.1038_s41467-024-53632-4.json"))
    assert abstract_from_inverted_index(work["abstract_inverted_index"]) == (
        "Abstract Generative drug design facilitates the creation of compounds "
        "effective against pathogenic target proteins."
    )
