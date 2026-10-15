"""Allow `python -m apr ...` as an alternative to the `apr` console script."""

from __future__ import annotations

from apr.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
