"""Bundled visual interface selected through the whab.plugins entry point."""

from pathlib import Path

from whab.plugins import Plugin, UserInterface

from .views import build_context

resources = Path(__file__).parent
plugin = Plugin(
    id="web",
    display_name="WHAB web",
    ui=UserInterface(
        templates=resources / "templates",
        static=resources / "static",
        build_context=build_context,
        budget_months=3,
        lightweight_transactions=True,
    ),
)
