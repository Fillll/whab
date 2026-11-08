"""Revision-protected agent annotations reject unsupported or ambiguous writes."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from whab.domain.errors import StoreError
from whab.services.ledger import LedgerService, RequestConflict


def test_agent_intent_is_atomic_idempotent_and_independent_of_review(store):
    service = LedgerService(store)
    account = service.add_account(
        "personal", {"name": "Fictional", "type": "depository", "balance": 100000}
    )
    identifier = service.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-01",
            "merchant": "Fictional shop",
            "amount": 1234,
            "categoryId": None,
        },
    )
    original = service.revision
    payload = {"transactionId": identifier, "categoryId": "groceries", "categoryAgent": "Codex"}
    result = service.command(
        "edit_transaction",
        "personal",
        payload,
        request_id="fictional-agent",
        expected_revision=original,
    )
    assert (
        service.command(
            "edit_transaction",
            "personal",
            payload,
            request_id="fictional-agent",
            expected_revision=original,
        )
        == result
    )
    assert service.transactions("personal")[0]["categoryAgent"] == "Codex"
    with pytest.raises(RequestConflict):
        service.command(
            "edit_transaction",
            "personal",
            {**payload, "categoryAgent": "Other"},
            request_id="fictional-agent",
            expected_revision=original,
        )
    service.edit_transaction("personal", identifier, reviewed=True)
    assert service.transactions("personal")[0]["categoryAgent"] == "Codex"
    before = deepcopy(store.export_budget("personal"))
    revision = service.revision
    for changes in [
        {"categoryAgent": "Other"},
        {"categoryId": "groceries", "categoryAgent": " bad"},
    ]:
        with pytest.raises(StoreError):
            service.command(
                "edit_transaction",
                "personal",
                {"transactionId": identifier, **changes},
                request_id="invalid-intent",
                expected_revision=revision,
            )
        assert store.export_budget("personal") == before and service.revision == revision
    service.edit_transaction("personal", identifier, category_id="dining")
    assert "categoryAgent" not in service.transactions("personal")[0]


@pytest.mark.parametrize(
    "backend", [SimpleNamespace(), SimpleNamespace(supports_category_agent=lambda: False)]
)
def test_older_storage_rejects_agent_labels_before_any_mutation(backend):
    service = LedgerService(backend)
    revision = service.revision
    with pytest.raises(StoreError, match="storage plugin"):
        service.command(
            "edit_transaction",
            "personal",
            {"transactionId": "fictional", "categoryId": "groceries", "categoryAgent": "Codex"},
            request_id="fictional-agent",
            expected_revision=revision,
        )
    assert service.revision == revision
