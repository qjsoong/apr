"""The record type is the merge rule for the whole pipeline; test it directly."""

from __future__ import annotations

from apr.model import (
    SOURCE_OK,
    STATUS_IDENTIFIED,
    STATUS_PROBABLE,
    STATUS_UNIDENTIFIED,
    PaperRecord,
    SourceAttempt,
)


def test_higher_confidence_replaces_lower() -> None:
    record = PaperRecord()
    assert record.set_field("title", "A", source="pdf-info", method="m", confidence=0.4)
    assert record.set_field("title", "B", source="arxiv", method="api", confidence=0.9)
    assert record.title == "B"
    assert record.provenance["title"].source == "arxiv"


def test_lower_confidence_does_not_replace() -> None:
    record = PaperRecord()
    record.set_field("title", "A", source="arxiv", method="api", confidence=0.9)
    assert not record.set_field(
        "title", "B", source="pdf-info", method="m", confidence=0.4
    )
    assert record.title == "A"
    assert [item.applied for item in record.evidence] == [True, False]
    assert "kept earlier value from arxiv" in (record.evidence[1].detail or "")


def test_force_overrides() -> None:
    record = PaperRecord()
    record.set_field("title", "A", source="arxiv", method="api", confidence=0.9)
    assert record.set_field(
        "title", "B", source="crossref", method="m", confidence=0.1, force=True
    )
    assert record.title == "B"


def test_empty_values_are_ignored() -> None:
    record = PaperRecord()
    for value in (None, "", "   ", [], {}):
        assert not record.set_field(
            "title", value, source="pdf-info", method="m", confidence=0.9
        )
    assert record.title is None
    assert record.evidence == []


def test_authors_are_cleaned_and_deduplicated() -> None:
    record = PaperRecord()
    record.set_field(
        "authors",
        [" Ada  Lovelace ", "Ada Lovelace", "", "Alan Turing"],
        source="crossref",
        method="m",
        confidence=0.9,
    )
    assert record.authors == ["Ada Lovelace", "Alan Turing"]


def test_year_is_coerced_and_bounded() -> None:
    record = PaperRecord()
    record.set_field(
        "year", "2024-03-01", source="crossref", method="m", confidence=0.9
    )
    assert record.year == 2024
    other = PaperRecord()
    assert not other.set_field("year", "n/a", source="x", method="m", confidence=0.9)
    assert not other.set_field("year", "12", source="x", method="m", confidence=0.9)


def test_unknown_field_is_a_programming_error() -> None:
    record = PaperRecord()
    try:
        record.set_field("nickname", "x", source="s", method="m", confidence=1.0)
    except KeyError as exc:
        assert "nickname" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown field must raise")


def test_status_without_title_is_unidentified() -> None:
    record = PaperRecord()
    record.set_field("doi", "10.1/x", source="pdf-info", method="m", confidence=1.0)
    assert record.status == STATUS_UNIDENTIFIED


def test_status_needs_confidence_and_authors_to_be_identified() -> None:
    record = PaperRecord()
    record.set_field("title", "T", source="pdf-first-page", method="m", confidence=0.85)
    assert record.status == STATUS_PROBABLE
    record.set_field(
        "authors", ["A"], source="pdf-first-page", method="m", confidence=0.6
    )
    assert record.status == STATUS_IDENTIFIED


def test_confidence_renormalises_over_present_fields() -> None:
    record = PaperRecord()
    record.set_field("title", "T", source="s", method="m", confidence=0.5)
    assert record.confidence == 0.5
    record.set_field("authors", ["A"], source="s", method="m", confidence=1.0)
    assert record.confidence == (0.45 * 0.5 + 0.35 * 1.0) / 0.8


def test_to_dict_shape_is_stable() -> None:
    record = PaperRecord()
    record.set_file_info(path="p.pdf", size=10, sha256="abc", pages=3)
    record.set_field("title", "T", source="s", method="m", confidence=0.8)
    record.add_source(SourceAttempt(source="arxiv", status=SOURCE_OK, candidates=1))
    payload = record.to_dict()
    assert list(payload) == [
        "schema_version",
        "apr_version",
        "path",
        "status",
        "confidence",
        "bytes",
        "sha256",
        "pages",
        "title",
        "authors",
        "year",
        "venue",
        "doi",
        "arxiv_id",
        "arxiv_version",
        "url",
        "abstract",
        "provenance",
        "sources",
    ]
    assert payload["authors"] == []
    assert payload["doi"] is None
    assert payload["sources"][0]["status"] == "ok"
    assert "evidence" not in payload
    assert "warnings" not in payload


def test_to_dict_can_drop_abstract_and_add_evidence() -> None:
    record = PaperRecord()
    record.set_field("abstract", "long text", source="s", method="m", confidence=1.0)
    record.set_field("title", "T", source="s", method="m", confidence=1.0)
    payload = record.to_dict(include_abstract=False, include_evidence=True)
    assert payload["abstract"] is None
    assert "abstract" not in payload["provenance"]
    assert [item["field"] for item in payload["evidence"]] == ["abstract", "title"]


def test_warnings_are_deduplicated() -> None:
    record = PaperRecord()
    record.warn("truncated title")
    record.warn("truncated title")
    assert record.warnings == ["truncated title"]
