"""Bounded register services retain validation and legacy storage behavior."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from whab.domain.errors import StoreError
from whab.services.ledger import LedgerService


def fixture_rows():
    return [
        {"id": f"row-{index:03}", "accountId": ("one", "two", "euro")[index % 3],
         "date": "2026-09-30" if index < 10 else "2026-10-01",
         "merchant": "Straße %_" if index == 0 else "Purchase",
         "note": "STRASSE receipt" if index == 0 else f"Note {index}",
         "pending": index % 7 == 0, "reviewed": index % 4 == 0,
         "currency": "EUR" if index % 3 == 2 else "USD", "cursor": "secret"}
        for index in range(325)
    ]


def legacy_store():
    return SimpleNamespace(
        accounts=lambda _: [{"id": key, "currency": currency} for key, currency in (
            ("one", "USD"), ("two", "USD"), ("euro", "EUR"),
        )],
        categories=lambda _: [], transactions=lambda *_: fixture_rows(),
    )


@pytest.mark.parametrize("options", [
    {}, {"page": 2}, {"page": 9999}, {"page_size": 17}, {"search": " strasse "},
    {"search": "straße"}, {"search": "%_"}, {"search": "never matches"},
    {"review": "needs-review"}, {"review": "reviewed", "page_size": 7, "page": 3},
    {"account_ids": ["one", "two", "one"]}, {"account_ids": []}, {"account_id": "euro"},
    {"account_id": "missing"}, {"account_id": "one"}, {"account_ids": ["missing"]},
    {"start_date": "2026-10-01", "end_date": "2026-10-01"},
])
def test_legacy_storage_pages_complete_selected_history(options):
    service = LedgerService(legacy_store())
    result = service.transaction_page("personal", None, "USD", **options)
    scoped = service.transaction_register(
        "personal", None, "USD", **{key: value for key, value in options.items()
                                    if key in {"account_id", "account_ids", "start_date", "end_date"}},
    )["transactions"]
    query, review = options.get("search", "").strip().casefold(), options.get("review", "all")
    matching = sorted(
        [row for row in scoped if (not query or query in f"{row['merchant']} {row['note']}".casefold())
         and (review != "needs-review" or not row["reviewed"])
         and (review != "reviewed" or row["reviewed"])],
        key=lambda row: (row["date"], row["id"]), reverse=True,
    )
    size = options.get("page_size", 100)
    pages = max(1, (len(matching) + size - 1) // size)
    page = min(options.get("page", 1), pages)
    offset = (page - 1) * size
    assert result["transactions"] == matching[offset:offset + size]
    assert result["transactionPage"] == {
        "total": len(scoped), "count": len(matching), "page": page, "pages": pages,
        "start": offset + 1 if matching else 0, "end": min(offset + size, len(matching)),
        "toReview": sum(not row["pending"] and not row["reviewed"] for row in scoped),
    }
    assert result["toReview"] == result["transactionPage"]["toReview"]
    assert "cursor" not in repr(result)


@pytest.mark.parametrize("month,options", [
    (None, {}), ("2026-10", {}), (None, {"search": "strasse"}),
    (None, {"search": "%_"}), (None, {"review": "needs-review"}),
    (None, {"review": "reviewed"}), (None, {"account_ids": []}),
    ("2026-10", {"end_date": "2026-09-30"}),
])
def test_sqlite_capability_matches_service_legacy_fallback(store, month, options):
    account = store.add_account("personal", {"name": "Wallet", "type": "depository", "balance": 0})
    for index, date in enumerate(("2026-09-30", "2026-10-01", "2026-10-02")):
        identifier = store.add_transaction("personal", {
            "accountId": account, "date": date, "merchant": "Straße %_" if not index else "Purchase",
            "amount": 100, "categoryId": None,
        })
        if index == 1:
            store.edit_transaction("personal", identifier, reviewed=True)
    legacy = SimpleNamespace(
        accounts=store.accounts, transaction_register=store.transaction_register,
        transaction_register_filtered=store.transaction_register_filtered,
    )
    assert LedgerService(store).transaction_page("personal", month, "USD", page_size=1, page=99, **options) == (
        LedgerService(legacy).transaction_page("personal", month, "USD", page_size=1, page=99, **options)
    )


@pytest.mark.parametrize("changes", [
    {"workspace": "invalid"}, {"month": "2026-13"}, {"month": "1899-12"},
    {"currency": "bad"}, {"account_id": []}, {"account_ids": "one"},
    {"account_ids": [None]}, {"account_ids": ["one"] * 501},
    {"start_date": "2026-02-30"}, {"end_date": "bad"},
    {"start_date": "2026-10-02", "end_date": "2026-10-01"},
    {"search": None}, {"search": "x" * 2001}, {"review": "bad"}, {"review": []},
    {"page": True}, {"page": 0}, {"page": "1"}, {"page_size": False},
    {"page_size": 0}, {"page_size": 1001}, {"page_size": 1.5},
])
def test_invalid_page_requests_do_not_dispatch(changes):
    service = LedgerService(SimpleNamespace(
        transaction_register_page=lambda *_: pytest.fail("Invalid request dispatched"),
    ))
    request = {"workspace": "personal", "month": None, "currency": "USD", **changes}
    with pytest.raises(StoreError):
        service.transaction_page(**request)


def page_projection():
    return {
        "accounts": [{"id": "one", "currency": "USD", "token": "secret"},
                     {"id": "euro", "currency": "EUR"}],
        "categories": [],
        "transactions": [{"id": "visible", "accountId": "one", "date": "2026-10-05",
                          "merchant": "Straße", "note": "Receipt", "pending": False,
                          "reviewed": True, "currency": "USD", "access_token": "secret"}],
        "transactionPage": {"total": 2, "count": 1, "page": 1, "pages": 1,
                            "start": 1, "end": 1, "toReview": 1},
        "cursor": "secret",
    }


def test_optional_page_receives_intersected_dates_and_deduplicated_accounts():
    calls = []
    data = page_projection()
    service = LedgerService(SimpleNamespace(transaction_register_page=lambda *args: calls.append(args) or data))
    result = service.transaction_page(
        "personal", "2026-10", "USD", account_ids=["one", "one"],
        start_date="2026-09-01", end_date="2026-11-01", search=" strasse ", review="reviewed", page=999,
    )
    assert calls == [("personal", "2026-10-01", "2026-10-31", "USD", ("one",),
                      " strasse ", "reviewed", 999, 100)]
    assert [row["id"] for row in result["accounts"]] == ["one"]
    assert result["currencies"] == ["EUR", "USD"]
    assert result["toReview"] == 1
    assert "secret" not in repr(result)
    assert data["transactions"][0]["access_token"] == "secret"


@pytest.mark.parametrize("account,selected", [("one", ("one",)), ("euro", None), ("missing", None)])
def test_optional_page_preserves_legacy_account_alias_scope(account, selected):
    calls = []
    data = page_projection()
    service = LedgerService(SimpleNamespace(
        accounts=lambda _: data["accounts"],
        transaction_register_page=lambda *args: calls.append(args) or data,
    ))
    service.transaction_page("personal", None, "USD", account, search="strasse")
    assert calls[0][4] == selected


@pytest.mark.parametrize("mutation", [
    lambda data: data["transactions"][0].update(accountId="foreign"),
    lambda data: data["transactions"][0].update(currency="EUR"),
    lambda data: data["transactions"][0].update(date="2026-09-30"),
    lambda data: data["transactions"][0].update(date="2026-11-01"),
    lambda data: data["transactions"][0].update(merchant="Unrelated"),
    lambda data: data["transactions"][0].update(reviewed=False),
    lambda data: data["transactions"][0].update(deleted=True),
    lambda data: data["transactions"][0].update(pending="false"),
    lambda data: data["transactions"][0].update(note=None),
    lambda data: data["transactionPage"].update(total=True),
    lambda data: data["transactionPage"].update(total=0),
    lambda data: data["transactionPage"].update(toReview=3),
    lambda data: data["transactionPage"].update(page=2),
    lambda data: data["transactionPage"].update(start=0),
    lambda data: data.update(transactions=[]),
    lambda data: data.update(transactions=None),
    lambda data: data["transactionPage"].pop("count"),
])
def test_contradictory_optional_pages_fail_closed(mutation):
    data = page_projection()
    mutation(data)
    service = LedgerService(SimpleNamespace(transaction_register_page=lambda *_: data))
    with pytest.raises(StoreError, match="invalid transaction page"):
        service.transaction_page("personal", "2026-10", "USD", account_ids=["one"],
                                 search="strasse", review="reviewed")


def test_duplicate_optional_rows_are_rejected_and_valid_rows_sorted():
    data = page_projection()
    data["transactions"].append({**data["transactions"][0], "id": "zzz"})
    data["transactionPage"].update(count=2, end=2)
    service = LedgerService(SimpleNamespace(transaction_register_page=lambda *_: data))
    result = service.transaction_page("personal", None, "USD", search="strasse")
    assert [row["id"] for row in result["transactions"]] == ["zzz", "visible"]
    data["transactions"][1]["id"] = "visible"
    with pytest.raises(StoreError, match="invalid transaction page"):
        service.transaction_page("personal", None, "USD", search="strasse")


def test_empty_optional_page_has_zero_positions_and_uses_shared_lock():
    class Lock:
        held = False

        def __enter__(self):
            assert not self.held
            self.held = True

        def __exit__(self, *args):
            self.held = False

    lock = Lock()
    data = page_projection()
    data["transactions"] = []
    data["transactionPage"].update(total=0, count=0, start=0, end=0, toReview=0)

    def read(*args):
        assert lock.held
        assert args[4] == ()
        return data

    result = LedgerService(SimpleNamespace(transaction_register_page=read), lock=lock).transaction_page(
        "personal", None, "USD", account_ids=[],
    )
    assert result["transactions"] == [] and result["transactionPage"]["start"] == 0
    assert not lock.held


@pytest.mark.parametrize("options", [
    {"account_ids": []}, {"account_ids": ["missing"]}, {"currency": "GBP"},
    {"month": "2026-10", "end_date": "2026-09-30"},
])
def test_optional_empty_scopes_cannot_return_other_selections_counts(options):
    data = page_projection()
    data["transactions"] = []
    data["transactionPage"].update(count=0, start=0, end=0)
    request = {"workspace": "personal", "month": None, "currency": "USD", "search": "missing",
               **options}
    service = LedgerService(SimpleNamespace(transaction_register_page=lambda *_: data))
    with pytest.raises(StoreError, match="invalid transaction page"):
        service.transaction_page(**request)


def test_optional_unfiltered_selection_must_have_matching_total_and_count():
    service = LedgerService(SimpleNamespace(transaction_register_page=lambda *_: page_projection()))
    with pytest.raises(StoreError, match="invalid transaction page"):
        service.transaction_page("personal", None, "USD")


def test_optional_workspace_periods_batch_is_sanitized_and_boundary_clamped():
    calls = []
    data = [{"month": "2200-12", "nested": {"cursor": "secret", "safe": True}}]
    service = LedgerService(SimpleNamespace(workspace_periods=lambda *args: calls.append(args) or data))
    original = deepcopy(data)
    assert service.workspace_periods("personal", "2200-12", 12) == [
        {"month": "2200-12", "nested": {"safe": True}},
    ]
    assert calls == [("personal", "2200-12", 1)]
    assert data == original
