"""Bundled Plaid plugin descriptor; loaded only when explicitly enabled or by default."""

from pathlib import Path

from whab.plugins import BankUI, MerchantLogos, Plugin

from .logos import fetch


def create_bank(context):
    # No HTTP client or token access occurs during entry-point discovery.
    from .service import PlaidService

    return PlaidService(
        context.store,
        {
            **context.config,
            "PLAID_ENV": context.environment,
            "TOKEN_ENCRYPTION_KEY": context.encryption_key,
        },
    )


assets = Path(__file__).parent
plugin = Plugin(
    id="plaid",
    display_name="Plaid",
    bank=create_bank,
    bank_ui=BankUI(templates=assets / "templates", static=assets / "static"),
    merchant_logos=MerchantLogos(fetch=fetch),
)
