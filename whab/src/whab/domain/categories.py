"""Two-level categories: named parent groups and assignable child categories.

The stored child record keeps its original ``group`` label for storage and plugin
compatibility. Parent identities and public trees are derived in core, so existing
ledgers acquire an explicit hierarchy without rewriting their category IDs.
"""

from collections.abc import Iterable, Mapping
from hashlib import sha256

from .errors import StoreError
from .money import currency_code, minor_units
from .validation import validate_label

GROUP_ID_PREFIX = "category-group:"
_CHILD_FIELDS = {"id", "name", "group", "target"}


def _label(value: object) -> str:
    label = validate_label(value)
    if "\x00" in label:
        raise StoreError("Invalid category name.")
    return label


def _identifier(value: object) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 200
        or "\x00" in value
        or value.startswith(GROUP_ID_PREFIX)
    ):
        raise StoreError("Invalid child category identifier.")
    return value


def category_group_id(name: str) -> str:
    """Return a deterministic parent identity, distinct from all child IDs."""
    return GROUP_ID_PREFIX + sha256(_label(name).encode("utf-8")).hexdigest()


def validate_child_category(category: dict) -> dict:
    """Validate a stored or public leaf record and return an independent copy."""
    if (
        type(category) is not dict
        or not _CHILD_FIELDS <= category.keys()
        or category.keys() - _CHILD_FIELDS - {"parentId", "currency"}
    ):
        raise StoreError("Categories must be children of one parent group.")
    _identifier(category["id"])
    _label(category["name"])
    _label(category["group"])
    currency_code(category.get("currency", "USD"))
    if minor_units(category["target"], category.get("currency", "USD")) < 0:
        raise StoreError("Target cannot be negative.")
    if "parentId" in category and category["parentId"] != category_group_id(category["group"]):
        raise StoreError("Category parent does not match its group.")
    return category.copy()


def _children(categories: Mapping[str, dict] | Iterable[dict]) -> list[dict]:
    records = categories.values() if isinstance(categories, Mapping) else categories
    children = [validate_child_category(category) for category in records]
    identifiers = [category["id"] for category in children]
    if len(set(identifiers)) != len(identifiers):
        raise StoreError("Duplicate child category identifier.")
    return children


def category_groups(categories: Mapping[str, dict] | Iterable[dict]) -> list[dict]:
    """Project unique nonassignable parents in their first-seen ledger order."""
    groups = {}
    for category in _children(categories):
        group_id = category_group_id(category["group"])
        groups.setdefault(group_id, {"id": group_id, "name": _label(category["group"])})
    return list(groups.values())


def public_categories(categories: Mapping[str, dict] | Iterable[dict]) -> list[dict]:
    """Expose assignable children with their explicit parent references."""
    return [
        {
            **category,
            "parentId": category_group_id(category["group"]),
            "currency": currency_code(category.get("currency", "USD")),
        }
        for category in _children(categories)
    ]


def category_tree(categories: Mapping[str, dict] | Iterable[dict]) -> list[dict]:
    """Expose exactly two levels; parent groups contain child leaves only."""
    groups = {}
    for category in public_categories(categories):
        group_id = category["parentId"]
        group = groups.setdefault(
            group_id, {"id": group_id, "name": _label(category["group"]), "children": []}
        )
        group["children"].append(category)
    return list(groups.values())


def create_category(
    category_id: str,
    name: str,
    group: str | None = None,
    target: int = 0,
    *,
    categories: Mapping[str, dict] | Iterable[dict] = (),
    parent_id: str | None = None,
    currency: str = "USD",
) -> dict:
    """Create an assignable child by parent label or an existing parent ID."""
    category_id = _identifier(category_id)
    name = _label(name)
    currency = currency_code(currency)
    target = minor_units(target, currency)
    if target < 0:
        raise StoreError("Target cannot be negative.")
    existing = _children(categories)
    if any(category["id"] == category_id for category in existing):
        raise StoreError("Category already exists.")
    if parent_id is not None:
        parents = {parent["id"]: parent for parent in category_groups(existing)}
        if type(parent_id) is not str or parent_id not in parents:
            raise StoreError("Parent category group not found.")
        parent_name = parents[parent_id]["name"]
        if group is not None and _label(group) != parent_name:
            raise StoreError("Category parent does not match its group.")
        group = parent_name
    return {
        "id": category_id, "name": name, "group": _label(group), "target": target,
        "currency": currency,
    }


def ensure_child_category(categories: Mapping[str, dict], category_id: str) -> dict:
    """Resolve only a child for a transaction, envelope assignment, or target."""
    if type(category_id) is not str or category_id not in categories:
        raise StoreError("Category not found. Only child categories can be assigned.")
    category = validate_child_category(categories[category_id])
    if category["id"] != category_id:
        raise StoreError("Invalid child category identifier.")
    return category
