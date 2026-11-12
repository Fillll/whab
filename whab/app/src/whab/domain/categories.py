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


def validate_category_groups(groups):
    """Validate explicit nonassignable parents, including empty groups."""
    if type(groups) is not list or len(groups) > 10000:
        raise StoreError('Invalid category groups.')
    result = []
    identifiers = set()
    for group in groups:
        if (type(group) is not dict or group.keys() != {'id', 'name'}
                or group['id'] != category_group_id(group['name']) or group['id'] in identifiers):
            raise StoreError('Invalid or duplicate category group.')
        identifiers.add(group['id'])
        result.append(group.copy())
    return result


def category_groups(categories: Mapping[str, dict] | Iterable[dict], saved=()) -> list[dict]:
    """Project unique nonassignable parents in their first-seen ledger order."""
    groups = {row['id']: row for row in validate_category_groups(list(saved) if type(saved) is tuple else saved)}
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


def category_tree(categories: Mapping[str, dict] | Iterable[dict], saved=()) -> list[dict]:
    """Expose exactly two levels; parent groups contain child leaves only."""
    children = public_categories(categories)
    groups = {row['id']: {**row, 'children': []} for row in category_groups(children, saved)}
    for category in children:
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
    groups=(),
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
        parents = {parent["id"]: parent for parent in category_groups(existing, groups)}
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


def add_category_group(state, name):
    """Create a real empty parent; never manufacture a placeholder child."""
    name = _label(name)
    groups = category_groups(state['categories'], state.get('categoryGroups', ()))
    if any(row['name'].strip().casefold() == name.strip().casefold() for row in groups):
        raise StoreError('Category group already exists. Use its name or ID.')
    identifier = category_group_id(name)
    state['categoryGroups'] = [*groups, {'id': identifier, 'name': name}]
    return identifier


def rename_category(state, category_id, name):
    """Change only one existing child's label; retain all financial references."""
    ensure_child_category(state['categories'], category_id)
    child = state['categories'][category_id]
    name = _label(name)
    if any(row['id'] != category_id
           and category_group_id(row['group']) == category_group_id(child['group'])
           and row['name'].strip().casefold() == name.casefold()
           for row in state['categories'].values()):
        raise StoreError('A category with this name already exists under this parent.')
    child['name'] = name


def move_category(state, category_id, parent_id):
    """Reparent one leaf without replacing its identity, money or references."""
    child = ensure_child_category(state['categories'], category_id)
    groups = category_groups(state['categories'], state.get('categoryGroups', ()))
    parent = next((row for row in groups if row['id'] == parent_id), None)
    if parent is None:
        raise StoreError('Parent category group not found. Only two levels are supported.')
    if any(row['id'] != category_id and row['group'] == parent['name']
           and row['name'].strip().casefold() == child['name'].strip().casefold()
           for row in state['categories'].values()):
        raise StoreError('A category with this name already exists in the destination group.')
    state['categoryGroups'] = groups
    state['categories'][category_id] = {
        **{key: value for key, value in child.items() if key != 'parentId'},
        'group': parent['name'],
    }


def ensure_child_category(categories: Mapping[str, dict], category_id: str) -> dict:
    """Resolve only a child for a transaction, envelope assignment, or target."""
    if type(category_id) is not str or category_id not in categories:
        raise StoreError("Category not found. Only child categories can be assigned.")
    category = validate_child_category(categories[category_id])
    if category["id"] != category_id:
        raise StoreError("Invalid child category identifier.")
    return category


def category_deletion_status(state, category_id, *, remove_zero_assignments=False):
    """Explain only the addressed child's references, without exposing peers."""
    ensure_child_category(state['categories'], category_id)
    references = [row for row in state['transactions'].values() if row.get('categoryId') == category_id]
    deleted = sum(bool(row.get('deleted', False)) for row in references)
    months = sorted(month for month, rows in state['budget'].items() if category_id in rows)
    zero = [month for month in months if state['budget'][month][category_id] == 0]
    nonzero = [month for month in months if state['budget'][month][category_id] != 0]
    return {'categoryId': category_id, 'deletable': not references and not nonzero and (not zero or remove_zero_assignments),
            'transactions': {'total': len(references), 'live': len(references) - deleted, 'deleted': deleted},
            'assignments': {'count': len(months), 'months': months, 'zeroMonths': zero,
                            'nonzeroCount': len(nonzero), 'nonzeroMonths': nonzero}}


def delete_category(state, category_id, *, remove_zero_assignments=False):
    """Remove one unreferenced leaf, retaining its parent and all financial data."""
    # Older persistence adapters only remove the category row. They must remain
    # conservative until they explicitly persist assignment removals atomically.
    status = category_deletion_status(state, category_id, remove_zero_assignments=remove_zero_assignments)
    if not status['deletable']:
        t, a = status['transactions'], status['assignments']
        if not t['total'] and not a['nonzeroCount']:
            raise StoreError('Update the storage adapter to delete zero-only assignment rows atomically with the category. No rows were removed.')
        raise StoreError(f"Cannot delete category: {t['live']} live and {t['deleted']} deleted transactions reference it; "
                         f"{a['nonzeroCount']} nonzero monthly assignments block deletion "
                         f"({len(a['zeroMonths'])} zero-valued rows remain unchanged). "
                         'No records or assignments were removed. Inspect category show for all relevant months.')
    groups = category_groups(state['categories'], state.get('categoryGroups', ()))
    state['categoryGroups'] = groups
    for month in status['assignments']['zeroMonths']:
        del state['budget'][month][category_id]
        if not state['budget'][month]:
            del state['budget'][month]
    del state['categories'][category_id]


def delete_category_group(state, parent_id):
    """Remove an explicit empty parent only; never cascade to its children."""
    groups = category_groups(state['categories'], state.get('categoryGroups', ()))
    if not any(row['id'] == parent_id for row in groups):
        raise StoreError('Parent category group not found.')
    if any(category_group_id(row['group']) == parent_id for row in state['categories'].values()):
        raise StoreError('Cannot delete category group: it still has children. Delete or move each child explicitly first.')
    state['categoryGroups'] = [row for row in groups if row['id'] != parent_id]
