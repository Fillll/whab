"""Explicit CLI identifiers, translated at the existing host boundary."""

from copy import deepcopy

_CLI_IDS = {"ynab": "ynab-zip-importer"}
_HOST_IDS = {value: key for key, value in _CLI_IDS.items()}


def host_id(identifier):
    return _HOST_IDS.get(identifier, identifier)


def cli_id(identifier):
    return _CLI_IDS.get(identifier, identifier)


def check_identifiers(catalog):
    if any(row["id"] in _HOST_IDS for row in catalog["plugins"]):
        raise ValueError("An installed plugin conflicts with a reserved CLI identifier.")


def catalog_identifiers(catalog):
    check_identifiers(catalog)
    value = deepcopy(catalog)
    for row in value["plugins"]:
        row["id"] = cli_id(row["id"])
        if row["documentation"]:
            row["documentation"]["plugin_id"] = cli_id(row["documentation"]["plugin_id"])
    return value


def plugin_identifiers(projection):
    value = deepcopy(projection)
    for row in value.get("plugins", []):
        row["id"] = cli_id(row["id"])
        for dependency in row.get("dependencies", []):
            dependency["id"] = cli_id(dependency["id"])
    for key in ("configured", "active"):
        if key in value:
            value[key] = [cli_id(identifier) for identifier in value[key]]
    return value
