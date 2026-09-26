"""Console entry point for the ``daylight`` command."""

from __future__ import annotations

from daylight.app import DaylightApp

__all__ = ["cli"]


def cli() -> None:
    """Run the Daylight Commander Textual application."""
    DaylightApp().run()


if __name__ == "__main__":
    cli()
