"""Device-local readable route aliases, separate from portable financial identities."""

import copy
import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import UUID

from whab.domain.errors import StoreError
from whab.web.navigation import name_slug, valid_route_key

from .document import atomic_write

MAX_ALIAS_BYTES = 8 * 1024 * 1024


class RouteAliases:
    """Retain old name aliases across renames and disambiguate within each scope."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self._scopes = self._read()

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            with self.path.open("rb") as source:
                raw = source.read(MAX_ALIAS_BYTES + 1)
            if len(raw) > MAX_ALIAS_BYTES:
                raise ValueError
            data = json.loads(raw)
            if type(data) is not dict or data.keys() != {"version", "scopes"} or type(data["version"]) is not int or data["version"] != 1:
                raise ValueError
            scopes = data["scopes"]
            if type(scopes) is not dict or len(scopes) > 4000:
                raise ValueError
            for scope, entries in scopes.items():
                if type(scope) is not str or len(scope) > 250 or type(entries) is not dict or len(entries) > 10000:
                    raise ValueError
                owners = {}
                for identifier, entry in entries.items():
                    if (
                        type(identifier) is not str or not identifier or len(identifier) > 200
                        or "\x00" in identifier or type(entry) is not dict
                        or entry.keys() != {"current", "aliases"}
                        or type(entry["aliases"]) is not list or not 1 <= len(entry["aliases"]) <= 100
                        or entry["current"] not in entry["aliases"]
                    ):
                        raise ValueError
                    for alias in entry["aliases"]:
                        if not valid_route_key(alias) or alias in owners:
                            raise ValueError
                        owners[alias] = identifier
            return scopes
        except (OSError, ValueError, TypeError):
            raise StoreError("The device-local budget address registry is unreadable. Preserve it before repairing the runtime directory.") from None

    def update(self, scope, records, *, reserved=()):
        """Return current aliases while keeping every previously published alias owned."""
        with self._lock:
            scopes = dict(self._scopes)
            entries = copy.deepcopy(scopes.get(scope, {}))
            scopes[scope] = entries
            owners = {**dict.fromkeys(reserved), **(reserved if isinstance(reserved, dict) else {}),
                      **{alias: identifier for identifier, row in entries.items() for alias in row["aliases"]}}
            used = set(owners)
            result = {}
            for record in sorted(records, key=lambda row: row["id"]):
                identifier = record["id"]
                base = name_slug(record.get("name")) or name_slug(identifier)
                if not base:
                    base = str(int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:8]))
                alias = base
                number = 2
                if scope == "budgets":
                    try:
                        if str(UUID(base)) == base and base != identifier:
                            used.add(base)
                            owners[base] = base
                    except ValueError:
                        pass
                while alias in used and owners.get(alias) != identifier:
                    alias = f"{base}-{number}"
                    number += 1
                row = entries.setdefault(identifier, {"current": alias, "aliases": []})
                if alias not in row["aliases"]:
                    row["aliases"].append(alias)
                    used.add(alias)
                    owners[alias] = identifier
                row["current"] = alias
                result[identifier] = alias
            if scopes != self._scopes:
                raw = json.dumps({"version": 1, "scopes": scopes}, ensure_ascii=True).encode()
                if len(raw) > MAX_ALIAS_BYTES or len(scopes) > 4000 or len(entries) > 10000 or any(len(row["aliases"]) > 100 for row in entries.values()):
                    raise StoreError("The device-local budget address registry is full.")
                try:
                    atomic_write(self.path, raw)
                except OSError:
                    raise StoreError("The device-local budget addresses could not be saved. Check the runtime directory.") from None
                self._scopes = scopes
            return result

    def resolve(self, scope, alias):
        with self._lock:
            return next((identifier for identifier, row in self._scopes.get(scope, {}).items() if alias in row["aliases"]), None)

    def aliases(self, scope, identifier):
        with self._lock:
            return list(self._scopes.get(scope, {}).get(identifier, {}).get("aliases", ()))
