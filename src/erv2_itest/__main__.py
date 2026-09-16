"""Entry point for the `erv2-itest` console script and `python -m erv2_itest`."""

from __future__ import annotations

from erv2_itest.cli import app


def main() -> None:
    """Run the erv2-itest CLI."""
    app()


if __name__ == "__main__":
    main()
