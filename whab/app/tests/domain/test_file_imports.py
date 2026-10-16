"""Whole-workspace import records reject malformed adapter payloads."""

from dataclasses import FrozenInstanceError, replace

import pytest

from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport, validate_workspace_import


def sample_import():
    return WorkspaceImport(
        accounts=({
            "id": "cash", "name": "Checking", "institution": "", "type": "depository",
            "balance": 100000, "source": "fixture", "stale": False, "balanceUnknown": False,
            "onBudget": True, "subtype": None,
        },),
        categories=({"id": "food", "name": "Food", "group": "Everyday", "target": 1000},),
        transactions=({
            "id": "purchase", "accountId": "cash", "date": "2026-10-01", "merchant": "Market",
            "amount": 500, "categoryId": "food", "pending": False, "reviewed": True,
            "source": "fixture", "note": "", "isTransfer": False, "inferredTransfer": False,
            "categoryEdited": True, "deleted": False,
        },),
        assignments=({"month": "2026-09", "categoryId": "food", "assigned": -100},),
        budget_start_month="2026-10",
        warnings=("Fixture warning",),
    )


def test_validated_payload_is_frozen_and_copies_all_mutable_records():
    data = sample_import()
    copied = data.validated_copy()
    assert copied == data
    for field in ("accounts", "categories", "transactions", "assignments"):
        records = getattr(data, field)
        assert getattr(copied, field)[0] is not records[0]
        records[0].clear()
        assert getattr(copied, field)[0]
    with pytest.raises(FrozenInstanceError):
        copied.budget_start_month = "2026-11"


def test_empty_workspace_and_uncategorized_history_are_valid():
    empty = WorkspaceImport((), (), (), (), "2026-10")
    assert empty.validated_copy() == empty
    data = sample_import()
    data.transactions[0]["categoryId"] = None
    data.transactions[0]["amount"] = -100
    assert data.validated_copy().transactions[0]["categoryId"] is None


@pytest.mark.parametrize("value", [None, {}, ()])
def test_non_workspace_payloads_are_rejected(value):
    with pytest.raises(StoreError, match="Invalid workspace import"):
        validate_workspace_import(value)


@pytest.mark.parametrize("field,limit", [
    ("accounts", 1000), ("categories", 10000), ("transactions", 250000),
    ("assignments", 250000),
])
def test_collections_require_bounded_tuples(field, limit):
    data = sample_import()
    for invalid in (list(getattr(data, field)), getattr(data, field) * (limit + 1)):
        with pytest.raises(StoreError, match="record collection"):
            replace(data, **{field: invalid}).validated_copy()


@pytest.mark.parametrize("field", ["accounts", "categories", "transactions", "assignments"])
@pytest.mark.parametrize("invalid", [None, {}, {"unexpected": "value"}])
def test_records_require_the_exact_public_fields(field, invalid):
    with pytest.raises(StoreError, match="record fields"):
        replace(sample_import(), **{field: (invalid,)}).validated_copy()


@pytest.mark.parametrize("field", ["accounts", "categories", "transactions"])
def test_record_ids_are_unique_within_each_kind(field):
    data = sample_import()
    with pytest.raises(StoreError, match="Duplicate.*identifier"):
        replace(data, **{field: getattr(data, field) * 2}).validated_copy()


@pytest.mark.parametrize("field,key,value", [
    ("accounts", "id", None), ("accounts", "id", ""), ("accounts", "id", "x" * 201),
    ("accounts", "id", "cash\x00"), ("accounts", "name", "  "),
    ("accounts", "institution", None), ("accounts", "source", "x" * 65),
    ("accounts", "type", []), ("accounts", "type", "unsupported"),
    ("accounts", "balance", True), ("accounts", "balance", 1.5),
    ("accounts", "balance", 1_000_000_000_001), ("accounts", "stale", 1),
    ("accounts", "balanceUnknown", None),
    ("categories", "name", ""), ("categories", "group", "x" * 201),
    ("categories", "target", -1), ("categories", "target", "100"),
    ("transactions", "accountId", "missing"), ("transactions", "accountId", []),
    ("transactions", "categoryId", "missing"), ("transactions", "categoryId", []),
    ("transactions", "date", "2026-02-30"), ("transactions", "date", "1800-01-01"),
    ("transactions", "date", 1), ("transactions", "merchant", ""),
    ("transactions", "amount", -1_000_000_000_001), ("transactions", "source", 1),
    ("transactions", "note", "x" * 1001), ("transactions", "note", None),
    ("transactions", "pending", "false"), ("transactions", "reviewed", 1),
    ("transactions", "isTransfer", None), ("transactions", "inferredTransfer", 0),
    ("transactions", "categoryEdited", "yes"), ("transactions", "deleted", []),
    ("assignments", "month", "2026-13"), ("assignments", "month", []),
    ("assignments", "categoryId", "missing"), ("assignments", "categoryId", []),
    ("assignments", "assigned", True),
])
def test_invalid_scalar_fields_and_references_fail(field, key, value):
    data = sample_import()
    getattr(data, field)[0][key] = value
    with pytest.raises(StoreError):
        data.validated_copy()


@pytest.mark.parametrize("kind,subtype", [
    ("investment", "brokerage"), ("retirement", "roth_ira"), ("property", "home"),
    ("loan", "mortgage"), ("deposit", "cd"), ("other", None),
])
def test_tracking_accounts_keep_known_balances_and_explicit_classification(kind, subtype):
    data = sample_import()
    data.accounts[0].update(type=kind, onBudget=False, subtype=subtype)
    restored = data.validated_copy().accounts[0]
    assert (restored["type"], restored["onBudget"], restored["subtype"]) == (kind, False, subtype)
    assert restored["balance"] == 100000


def test_legacy_workspace_accounts_receive_scope_and_subtype_defaults():
    data = sample_import()
    del data.accounts[0]["onBudget"]
    del data.accounts[0]["subtype"]
    assert data.validated_copy().accounts[0]["onBudget"] is True
    data.accounts[0]["type"] = "investment"
    assert data.validated_copy().accounts[0]["onBudget"] is False


@pytest.mark.parametrize("changes", [
    {"onBudget": "false"}, {"onBudget": 0}, {"subtype": "mortgage"},
    {"subtype": []}, {"type": "investment", "onBudget": True},
])
def test_workspace_classification_rejects_invalid_explicit_values(changes):
    data = sample_import()
    data.accounts[0].update(changes)
    with pytest.raises(StoreError):
        data.validated_copy()


def test_duplicate_assignments_and_unsafe_aggregate_totals_fail(monkeypatch):
    data = sample_import()
    with pytest.raises(StoreError, match="Duplicate.*assignment"):
        replace(data, assignments=data.assignments * 2).validated_copy()
    monkeypatch.setattr("whab.domain.file_imports.MAX_SAFE_INTEGER", 100)
    with pytest.raises(StoreError, match="totals exceed"):
        data.validated_copy()


@pytest.mark.parametrize("month", [None, "1800-01", "2026-13", "2026-1"])
def test_budget_start_requires_a_supported_month(month):
    with pytest.raises(StoreError):
        replace(sample_import(), budget_start_month=month).validated_copy()


@pytest.mark.parametrize("warnings", [[], ("valid",) * 101, (None,), ("",), ("x" * 1001,)])
def test_warnings_are_bounded_text(warnings):
    with pytest.raises(StoreError):
        replace(sample_import(), warnings=warnings).validated_copy()


def test_imported_group_labels_become_two_level_parents_without_changing_record_shape():
    from whab.domain.categories import category_group_id, category_tree

    data = sample_import().validated_copy()
    assert set(data.categories[0]) == {"id", "name", "group", "target"}
    group = category_tree(data.categories)[0]
    assert group["id"] == category_group_id("Everyday")
    assert group["children"][0]["id"] == "food"


@pytest.mark.parametrize("changes", [
    {"children": []}, {"parentId": "food"}, {"group": {"name": "Deeper"}},
    {"group": "Everyday\x00"},
])
def test_imported_categories_cannot_contain_parent_or_deeper_records(changes):
    data = sample_import()
    data.categories[0].update(changes)
    with pytest.raises(StoreError):
        data.validated_copy()


@pytest.mark.parametrize("field", ["transactions", "assignments"])
def test_imports_cannot_assign_parent_groups(field):
    from whab.domain.categories import category_group_id

    data = sample_import()
    getattr(data, field)[0]["categoryId"] = category_group_id("Everyday")
    with pytest.raises(StoreError, match="category reference"):
        data.validated_copy()


def test_imported_parent_identifier_cannot_masquerade_as_an_assignable_child():
    from whab.domain.categories import category_group_id

    data = sample_import()
    data.categories[0]["id"] = category_group_id("Everyday")
    with pytest.raises(StoreError, match="child category identifier"):
        data.validated_copy()
