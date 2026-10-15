"""Offline extraction against the real PDF corpus (``APR_CORPUS``).

Every expectation here was read off page 1 of the named file, or off its
embedded metadata, so the assertions describe the PDF rather than the current
implementation. The corpus is not redistributed; tests skip when a file is
absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apr.extract import extract_local
from apr.model import STATUS_UNIDENTIFIED

pytestmark = pytest.mark.corpus


def corpus_pdf(corpus_dir: Path, name: str) -> Path:
    """One corpus file, or a skip when this machine does not have it."""
    path = corpus_dir / name
    if not path.is_file():
        pytest.skip(f"corpus file not present: {name}")
    return path


def test_every_corpus_pdf_yields_a_title(corpus_dir: Path) -> None:
    """The acceptance bar: a title for every file, from the file alone."""
    results = {}
    for path in sorted(corpus_dir.glob("*.pdf")):
        record = extract_local(path)
        results[path.name] = (record.title, record.status)
    unidentified = [
        name
        for name, (title, status) in results.items()
        if not title or status == STATUS_UNIDENTIFIED
    ]
    assert unidentified == []
    assert len(results) >= 1


def test_arxiv_genpdf_truncated_metadata(corpus_dir: Path) -> None:
    """``2610.01939v2.pdf``: full page-1 title, complete author list."""
    record = extract_local(corpus_pdf(corpus_dir, "2610.01939v2.pdf"))
    assert record.title == (
        "Fewer Tokens, Better Action: GPT-6 Astra Robot Agents with 14% Higher "
        "Success Rate but 65% Fewer Tokens"
    )
    assert record.authors[0] == "Ruiyang Si"
    assert len(record.authors) == 15
    assert record.arxiv_id == "2610.01939"
    assert record.arxiv_version == "v2"
    assert record.year == 2026
    assert record.venue == "arXiv"
    assert record.doi == "10.48550/arxiv.2610.01939"
    assert any("truncated" in warning for warning in record.warnings)
    # The abstract block ends where the printed "1 Introduction" heading does,
    # so the introduction's first sentence must not be part of it.
    assert record.abstract is not None
    assert record.abstract.startswith("Vision-language model (VLM) agents")
    assert "surpassing the Qwen3.5-27B base model." in record.abstract
    assert "Code as Policies and ProgPrompt" not in record.abstract


def test_arxiv_genpdf_complete_metadata(corpus_dir: Path) -> None:
    """``2505.21469v4.pdf``: the embedded title is complete, so it is kept."""
    record = extract_local(corpus_pdf(corpus_dir, "2505.21469v4.pdf"))
    assert record.title == (
        "PropMolFlow: Property-Guided Molecule Generation with "
        "Geometry-Complete Flow Matching"
    )
    assert record.authors[0] == "Cheng Zeng"
    assert record.authors == [
        "Cheng Zeng",
        "Jirui Jin",
        "Connor Ambrose",
        "George Karypis",
        "Mark Transtrum",
        "Ellad B. Tadmor",
        "Richard G. Hennig",
        "Adrian Roitberg",
        "Stefano Martiniani",
        "Mingjie Liu",
    ]
    assert record.arxiv_id == "2505.21469"
    assert record.year == 2025
    assert record.provenance["title"].source == "pdf-info"
    assert record.abstract is not None
    assert record.abstract.endswith("out-of-distribution generalization.")


def test_arxiv_pdf_without_embedded_metadata(corpus_dir: Path) -> None:
    """``2306.12983v2.pdf``: empty ``/Title`` and ``/Author``, page 1 has both."""
    record = extract_local(corpus_pdf(corpus_dir, "2306.12983v2.pdf"))
    assert record.title == (
        "Towards More Realistic Membership Inference Attacks on Large Diffusion Models"
    )
    assert record.authors == [
        "Jan Dubiński",
        "Antoni Kowalczuk",
        "Stanisław Pawlak",
        "Przemyslaw Rokita",
        "Tomasz Trzcinski",
        "Paweł Morawiecki",
    ]
    assert record.arxiv_id == "2306.12983"
    assert record.year == 2023
    assert record.abstract is not None
    assert record.abstract.startswith("Generative diffusion models")
    assert record.abstract.endswith("will persist in the foreseeable future.")


def test_conference_pdf_with_caps_title(corpus_dir: Path) -> None:
    """``2310.16789v3.pdf``: caps title split over two lines, ICLR 2024 header."""
    record = extract_local(corpus_pdf(corpus_dir, "2310.16789v3.pdf"))
    assert record.title == ("DETECTING PRETRAINING DATA FROM LARGE LANGUAGE MODELS")
    assert record.authors[0] == "Weijia Shi"
    assert record.authors[-1] == "Luke Zettlemoyer"
    assert record.arxiv_id == "2310.16789"
    assert record.venue == "ICLR 2024"
    assert record.year == 2024
    assert record.abstract is not None
    assert record.abstract.endswith("consistently effective solution.")


def test_conference_pdf_with_hyphenated_caps_title(corpus_dir: Path) -> None:
    """The ICLR 2025 file: ``MOD-`` + ``ELS`` and a font-metric space artefact."""
    record = extract_local(
        corpus_pdf(
            corpus_dir,
            "ICLR-2025-min-k-improved-baseline-for-pre-training-data-detection"
            "-from-large-language-models-Paper-Conference.pdf",
        )
    )
    assert record.title == (
        "MIN-K%++: IMPROVED BASELINE FOR DETECTING PRE-TRAINING DATA "
        "FROM LARGE LANGUAGE MODELS"
    )
    assert record.authors[0] == "Jingyang Zhang"
    assert record.authors[-1] == "Hai Li"
    assert record.venue == "ICLR 2025"
    assert record.year == 2025
    assert record.abstract is not None
    assert record.abstract.endswith("requires an extra reference model.")


def test_nips_pdf_with_rich_info_dictionary(corpus_dir: Path) -> None:
    """``NIPS-2017-…``: complete ``/Author`` and a conference footer."""
    record = extract_local(
        corpus_pdf(corpus_dir, "NIPS-2017-attention-is-all-you-need-Paper.pdf")
    )
    assert record.title == "Attention Is All You Need"
    assert record.authors[0] == "Ashish Vaswani"
    assert len(record.authors) == 8
    assert record.authors[-1] == "Illia Polosukhin"
    assert record.venue == "NIPS 2017"
    assert record.year == 2017
    assert record.abstract is not None
    assert record.abstract.startswith("The dominant sequence transduction models")
    assert record.abstract.endswith("best models from the literature.")


def test_nature_article_with_partial_info_authors(corpus_dir: Path) -> None:
    """``s41467-024-53632-4.pdf``: ``/Author`` is one name, XMP has all of them."""
    record = extract_local(corpus_pdf(corpus_dir, "s41467-024-53632-4.pdf"))
    assert record.title == (
        "TamGen: drug design with target-aware molecule generation through a "
        "chemical language model"
    )
    assert record.doi == "10.1038/s41467-024-53632-4"
    assert record.venue == "Nature Communications"
    assert record.year == 2024
    assert record.authors[0] == "Kehan Wu"
    assert len(record.authors) == 23
    assert record.authors[-1] == "Tie-Yan Liu"
    assert any("XMP" in warning for warning in record.warnings)
    # This style has no abstract marker on page 1, so no abstract is claimed.
    assert record.abstract is None


def test_nature_articles_carry_their_doi_in_the_page_header(corpus_dir: Path) -> None:
    """``s41467-025-60763-9.pdf``: DOI from the page header, not from ``/doi``."""
    record = extract_local(corpus_pdf(corpus_dir, "s41467-025-60763-9.pdf"))
    assert record.doi == "10.1038/s41467-025-60763-9"
    assert record.title == (
        "A genotype-to-drug diffusion model for generation of tailored "
        "anti-cancer small molecules"
    )
    assert record.authors[0] == "Hyunho Kim"
    assert record.year == 2025


def test_nature_article_with_three_authors(corpus_dir: Path) -> None:
    """``s41467-026-75702-5.pdf``: short author list from XMP."""
    record = extract_local(corpus_pdf(corpus_dir, "s41467-026-75702-5.pdf"))
    assert record.doi == "10.1038/s41467-026-75702-5"
    assert record.authors == ["Md Atik Ahamed", "Qiang Ye", "Qiang Cheng"]
    assert record.title == (
        "Mol-CADiff: text-conditional molecule generation via causality-aware "
        "autoregressive diffusion"
    )


def test_nature_computational_science_journal_line(corpus_dir: Path) -> None:
    """``s43588-024-00737-x.pdf``: journal line gives venue and year."""
    record = extract_local(corpus_pdf(corpus_dir, "s43588-024-00737-x.pdf"))
    assert (
        record.title == "Structure-based drug design with equivariant diffusion models"
    )
    assert record.doi == "10.1038/s43588-024-00737-x"
    assert record.venue == "Nature Computational Science"
    assert record.year == 2024
    assert record.authors[0] == "Arne Schneuing"
    assert len(record.authors) == 13


def test_nature_computational_science_2026(corpus_dir: Path) -> None:
    """``s43588-026-00986-y.pdf``: an August 2026 issue."""
    record = extract_local(corpus_pdf(corpus_dir, "s43588-026-00986-y.pdf"))
    assert record.title == (
        "SmileyLlama: modifying large language models for directed chemical "
        "space exploration"
    )
    assert record.doi == "10.1038/s43588-026-00986-y"
    assert record.venue == "Nature Computational Science"
    assert record.year == 2026
    assert record.authors[0] == "Joseph M. Cavanagh"
    assert len(record.authors) == 7


def test_wacv_preprint_pair(corpus_dir: Path) -> None:
    """The WACV file of the same paper as ``2306.12983v2.pdf``.

    It has a complete ``/Info`` (ASCII names) and a ``/Subject`` venue, and no
    arXiv identifier; the year comes from its file name.
    """
    name = (
        "Dubinski_Towards_More_Realistic_Membership_Inference_Attacks_on_Large"
        "_Diffusion_Models_WACV_2024_paper.pdf"
    )
    record = extract_local(corpus_pdf(corpus_dir, name))
    assert record.title == (
        "Towards More Realistic Membership Inference Attacks on Large Diffusion Models"
    )
    assert record.authors[0] == "Jan Dubinski"
    assert len(record.authors) == 6
    assert record.venue == "IEEE Winter Conference on Applications of Computer Vision"
    assert record.year == 2024
    assert record.arxiv_id is None
