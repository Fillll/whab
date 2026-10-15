"""Fictional institution records shared by visual and host-boundary tests."""

import base64

import pytest

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jY3kAAAAASUVORK5CYII="
)
LOGO_PATH = "/api/plugins/logos/bank-logos/chase"


@pytest.fixture
def imported_store(store):
    store.save_item("synthetic-item", "SYNTHETIC_ENCRYPTED_TOKEN", "Chase")
    store.apply_sync(
        "synthetic-item",
        {"added": [], "modified": [], "removed": []},
        "synthetic-cursor",
        [
            {
                "id": account_id,
                "name": name,
                "institution": institution,
                "type": "depository",
                "balance": 12345,
                "currency": "USD",
                "source": "plaid",
                "itemId": "synthetic-item",
                "mask": "0000",
            }
            for account_id, name, institution in (
                ("synthetic-checking", "Fictional checking", "Chase"),
                ("synthetic-savings", "Fictional savings", "Chase"),
                ("synthetic-unknown", "Fictional account", "Unknown Fixture Bank"),
            )
        ],
    )
    return store
