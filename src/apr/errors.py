"""Errors that cross module boundaries."""

from __future__ import annotations


class AprError(Exception):
    """Base class for errors apr reports on stderr."""


class InputError(AprError):
    """The input cannot be read as a PDF (missing, unreadable, not a PDF)."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


class NetworkError(AprError):
    """A network lookup failed; providers record these instead of raising."""
