"""CLI wiring: argument handling, output shape and exit codes.

The pipeline is stubbed here so that these tests stay about the command line;
extraction has its own tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from apr import cli
from apr.errors import InputError
from apr.model import PaperRecord


def make_record(path: str, title: str | None = "A Title") -> PaperRecord:
    record = PaperRecord()
    record.set_file_info(path=path)
    if title:
        record.set_field(
            "title", title, source="pdf-first-page", method="layout", confidence=0.8
        )
        record.set_field(
            "authors",
            ["Ada Lovelace"],
            source="pdf-first-page",
            method="layout",
            confidence=0.7,
        )
    return record


@pytest.fixture
def stub_identify(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []

    def fake_identify(path: str | Path, **kwargs: object) -> PaperRecord:
        calls.append((str(path), bool(kwargs.get("online"))))
        return make_record(str(path))

    monkeypatch.setattr(cli, "identify", fake_identify)
    return calls


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("apr ")


def test_online_and_offline_are_mutually_exclusive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--online", "--offline", "a.pdf"])
    assert excinfo.value.code == 2
    assert "--online and --offline" in capsys.readouterr().err


def test_requires_an_input(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_single_file_prints_one_object(
    stub_identify: list[tuple[str, bool]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["paper.pdf"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["title"] == "A Title"
    assert payload["status"] == "identified"
    assert out.count("\n") == 1
    assert stub_identify == [("paper.pdf", False)]


def test_several_files_print_one_object_per_line(
    stub_identify: list[tuple[str, bool]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["a.pdf", "b.pdf"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["path"] for line in lines] == ["a.pdf", "b.pdf"]


def test_pretty_with_several_files_stays_valid_json(
    stub_identify: list[tuple[str, bool]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--pretty", "a.pdf", "b.pdf"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["path"] for item in payload] == ["a.pdf", "b.pdf"]


def test_offline_is_the_default_and_online_is_opt_in(
    stub_identify: list[tuple[str, bool]], capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["--online", "a.pdf"])
    capsys.readouterr()
    assert stub_identify == [("a.pdf", True)]


def test_unidentified_input_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "identify", lambda path, **kwargs: make_record(str(path), title=None)
    )
    assert cli.main(["a.pdf"]) == cli.EXIT_UNIDENTIFIED
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unidentified"


def test_unreadable_input_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(path: str | Path, **kwargs: object) -> PaperRecord:
        raise InputError(str(path), "not a PDF")

    monkeypatch.setattr(cli, "identify", boom)
    assert cli.main(["a.pdf"]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "apr: a.pdf: not a PDF" in captured.err
    assert captured.out == ""


def test_one_bad_file_does_not_sink_the_others(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def sometimes(path: str | Path, **kwargs: object) -> PaperRecord:
        if str(path) == "bad.pdf":
            raise InputError(str(path), "not a PDF")
        return make_record(str(path))

    monkeypatch.setattr(cli, "identify", sometimes)
    assert cli.main(["good.pdf", "bad.pdf"]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert json.loads(captured.out)["path"] == "good.pdf"
    assert "bad.pdf" in captured.err


def test_no_abstract_and_why_flags(
    stub_identify: list[tuple[str, bool]], capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["--no-abstract", "--why", "a.pdf"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["abstract"] is None
    assert payload["evidence"]


def test_unexpected_failure_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(path: str | Path, **kwargs: object) -> PaperRecord:
        raise ValueError("corrupt xref table")

    monkeypatch.setattr(cli, "identify", boom)
    assert cli.main(["a.pdf"]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "apr: a.pdf: internal error: ValueError: corrupt xref table" in captured.err
    assert "Traceback" not in captured.err


def test_unexpected_failure_shows_a_traceback_when_verbose(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(path: str | Path, **kwargs: object) -> PaperRecord:
        raise ValueError("corrupt xref table")

    monkeypatch.setattr(cli, "identify", boom)
    assert cli.main(["-v", "a.pdf"]) == cli.EXIT_ERROR
    assert "Traceback" in capsys.readouterr().err


def test_closed_pipe_exits_141(
    stub_identify: list[tuple[str, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenStdout:
        def write(self, _text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

        def fileno(self) -> int:
            return 1

    monkeypatch.setattr(cli.sys, "stdout", BrokenStdout())
    assert cli.main(["a.pdf"]) == cli.EXIT_BROKEN_PIPE


def test_python_dash_m_runs_the_cli() -> None:
    """`python -m apr` is the documented way to run an uninstalled checkout."""
    proc = subprocess.run(
        [sys.executable, "-m", "apr", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout.startswith("apr ")


def test_stdin_is_spooled_and_cleaned_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def fake_identify(path: str | Path, **kwargs: object) -> PaperRecord:
        seen.append(str(path))
        assert Path(path).read_bytes() == b"%PDF-1.4 fake"
        return make_record(str(path))

    monkeypatch.setattr(cli, "identify", fake_identify)
    monkeypatch.setattr(
        cli.sys,
        "stdin",
        type("S", (), {"buffer": __import__("io").BytesIO(b"%PDF-1.4 fake")})(),
    )
    assert cli.main(["-"]) == 0
    capsys.readouterr()
    assert seen and seen[0].endswith(".pdf")
    assert not Path(seen[0]).exists()
