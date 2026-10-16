"""Composition fixtures shared by core and individually selected plugin suites."""

import pytest


@pytest.fixture
def store():
    # Import only when a test requests the reference backend.
    from whab_sqlite import Store

    ledger = Store(":memory:", month="2026-10")
    yield ledger
    ledger.close()


@pytest.fixture
def legacy_token():
    # Synthetic Node-compatible token, never copied from a connected institution.
    return "AAECAwQFBgcICQoL.FFTb+H1beXLKIUNOoBfu7w==./LGogIH+lMvWJWUDjU/XreJ0vjs="
