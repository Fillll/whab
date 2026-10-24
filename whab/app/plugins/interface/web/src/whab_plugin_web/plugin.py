"""Bundled visual interface selected through the whab.plugins entry point."""

from pathlib import Path

from whab.plugins import Plugin, PluginContract, UserInterface

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
        lightweight_accounts=True,
        transaction_page_size=100,
        view_protocol=1,
    ),
    contract=PluginContract(lifecycle="live"),
)
