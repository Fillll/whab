"""A bounded fictional USD account file; only the host commits financial changes."""

import json

from whab.plugins import FileImport, Plugin, PluginContract, StoreError, WorkspaceImport


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def parse(raw):
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= 16384:
            raise ValueError()
        value = json.loads(raw, object_pairs_hook=_unique)
        if type(value) is not dict or set(value) != {"name", "balance"} or type(value["balance"]) is not int:
            raise ValueError()
        return WorkspaceImport(({
            "id": "fictional-account", "name": value["name"], "institution": "",
            "type": "depository", "balance": value["balance"], "source": "manual",
            "stale": False, "balanceUnknown": False,
        },), (), (), (), "2026-10").validated_copy()
    except (ValueError, TypeError, RecursionError, UnicodeError, StoreError):
        raise StoreError("Choose a bounded fictional USD account JSON file with name and integer balance.") from None


plugin = Plugin("example-file-import", "Fictional account import", file_import=FileImport(parse),
                contract=PluginContract(lifecycle="restart"))
