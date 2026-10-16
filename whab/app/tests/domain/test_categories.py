"""Category groups organize a fixed two-level hierarchy of assignable leaves."""

import pytest

from whab.domain.categories import (
    category_group_id,
    category_groups,
    category_tree,
    create_category,
    ensure_child_category,
    public_categories,
    validate_child_category,
)
from whab.domain.errors import StoreError


def categories():
    return {
        "food": {"id": "food", "name": "Food", "group": "Everyday", "target": 10000},
        "rent": {"id": "rent", "name": "Rent", "group": "Home", "target": 200000},
        "travel": {"id": "travel", "name": "Travel", "group": "Everyday", "target": 0},
    }


def test_parent_groups_are_stable_and_have_no_assignable_fields():
    records = categories()
    groups = category_groups(records)
    assert groups == [
        {"id": category_group_id("Everyday"), "name": "Everyday"},
        {"id": category_group_id("Home"), "name": "Home"},
    ]
    assert category_groups([records["rent"], records["food"]]) == list(reversed(groups))
    assert category_group_id("Everyday") == category_group_id(" Everyday ")
    assert category_group_id("Everyday") != category_group_id("everyday")
    assert category_group_id("Everyday") not in records


def test_public_hierarchy_is_exactly_two_levels_and_copies_mutable_records():
    records = categories()
    children = public_categories(records)
    assert children[0] == {
        **records["food"], "parentId": category_group_id("Everyday"), "currency": "USD"
    }
    tree = category_tree(records)
    assert [child["id"] for child in tree[0]["children"]] == ["food", "travel"]
    assert [child["id"] for child in tree[1]["children"]] == ["rent"]
    assert all("children" not in child for group in tree for child in group["children"])
    assert "parentId" not in records["food"]
    children[0]["name"] = "Updated"
    tree[0]["children"][0]["name"] = "Updated again"
    assert records["food"]["name"] == "Food"


def test_empty_hierarchy_has_no_parents_or_children():
    assert category_groups({}) == []
    assert public_categories(()) == []
    assert category_tree([]) == []


def test_creation_preserves_legacy_group_input_and_normalizes_new_labels():
    child = create_category("utilities", " Utilities ", " Home ", 15000)
    assert child == {
        "id": "utilities", "name": "Utilities", "group": "Home", "target": 15000,
        "currency": "USD",
    }
    assert ensure_child_category({child["id"]: child}, child["id"]) == child


def test_creation_can_select_an_existing_parent_id():
    parent_id = category_group_id("Everyday")
    child = create_category("coffee", "Coffee", categories=categories(), parent_id=parent_id)
    assert child["group"] == "Everyday"
    assert child["target"] == 0
    assert create_category(
        "coffee", "Coffee", " Everyday ", categories=categories(), parent_id=parent_id
    ) == child


@pytest.mark.parametrize("parent_id,group", [
    ("food", None), ("missing", None), ([], None),
    (category_group_id("Everyday"), "Home"),
])
def test_child_or_unknown_parent_and_mismatched_group_are_rejected(parent_id, group):
    with pytest.raises(StoreError, match="[Pp]arent"):
        create_category("coffee", "Coffee", group, categories=categories(), parent_id=parent_id)


@pytest.mark.parametrize("changes", [
    {"id": ""}, {"id": None}, {"id": []}, {"id": "x" * 201}, {"id": "bad\x00"},
    {"id": category_group_id("Everyday")}, {"name": ""}, {"name": "bad\x00"},
    {"group": ""}, {"group": None}, {"group": {"name": "Nested"}},
    {"target": True}, {"target": -1}, {"target": "100"},
    {"children": []}, {"parentId": "food"}, {"currency": "invalid"},
])
def test_invalid_or_deeper_children_are_rejected(changes):
    child = {**categories()["food"], **changes}
    with pytest.raises(StoreError):
        validate_child_category(child)


def test_parent_and_child_reference_rules_apply_even_to_corrupt_internal_state():
    records = categories()
    for category_id in (category_group_id("Everyday"), "missing", [], None):
        with pytest.raises(StoreError, match="Category not found"):
            ensure_child_category(records, category_id)
    records["parent"] = {"id": "parent", "name": "Parent", "children": []}
    with pytest.raises(StoreError, match="children"):
        ensure_child_category(records, "parent")
    records["mismatched"] = categories()["food"]
    with pytest.raises(StoreError, match="identifier"):
        ensure_child_category(records, "mismatched")


def test_creation_rejects_duplicate_child_ids_and_negative_targets():
    with pytest.raises(StoreError, match="already exists"):
        create_category("food", "Food", "Home", categories=categories())
    with pytest.raises(StoreError, match="negative"):
        create_category("coffee", "Coffee", "Home", -1)
    with pytest.raises(StoreError):
        create_category("coffee", "Coffee")


def test_duplicate_internal_child_ids_are_rejected_instead_of_projecting_ambiguity():
    records = tuple(categories().values())
    with pytest.raises(StoreError, match="Duplicate"):
        category_tree(records + records[:1])


def test_public_children_validate_parent_link_and_retain_currency_per_leaf():
    records = categories()
    records["food"]["currency"] = "EUR"
    projected = public_categories(records)
    assert validate_child_category(projected[0]) == projected[0]
    assert category_tree(projected)[0]["children"][0]["currency"] == "EUR"
    assert category_tree(projected)[0]["children"][1]["currency"] == "USD"
    assert create_category("coffee", "Coffee", "Everyday", currency="EUR")["currency"] == "EUR"
