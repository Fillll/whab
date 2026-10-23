"""One private durable decision for a visual selection and its approved worker.

UI choice is device-local, like the initial WHAB_UI configuration. A single
record selects both code and resource identity; ordinary optional-worker refs
are deliberately not a second source of visual authority.
"""

import re
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from packaging.version import Version

from .effect_store import EffectError, EffectStore, digest, encode, invalid
from .sdk import valid_plugin_name

_HASH = re.compile(r"[0-9a-f]{64}\Z")
DISABLED_GENERATION = digest(b"WHAB visual interface disabled:1")


def _hash(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        invalid()


def _worker(value, identifier):
    if value is None:
        return
    if type(value) is not dict or value.keys() != {"environment", "distribution", "version"} or identifier is None:
        invalid()
    _hash(value["environment"])
    if (type(value["distribution"]) is not str or not 1 <= len(value["distribution"]) <= 120
            or re.fullmatch(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*", value["distribution"]) is None
            or type(value["version"]) is not str or not 1 <= len(value["version"]) <= 80):
        invalid()
    Version(value["version"])


def _decision(value):
    if type(value) is not dict or value.keys() != {"id", "selection", "generation", "worker"}:
        invalid()
    if type(value["id"]) is not str or str(UUID(value["id"])) != value["id"]:
        invalid()
    if value["selection"] is not None and not valid_plugin_name(value["selection"]):
        invalid()
    _hash(value["generation"])
    if value["selection"] is None and value["generation"] != DISABLED_GENERATION:
        invalid()
    _worker(value["worker"], value["selection"])


def _validate(value):
    if (type(value) is not dict or value.keys() != {"format", "selection", "generation", "worker", "known", "workers", "decisions"}
            or type(value["format"]) is not int or value["format"] != 1):
        invalid()
    if value["selection"] is not None and not valid_plugin_name(value["selection"]):
        invalid()
    if value["generation"] is not None:
        _hash(value["generation"])
        if value["selection"] is None and value["generation"] != DISABLED_GENERATION:
            invalid()
    _worker(value["worker"], value["selection"])
    if value["worker"] is not None and value["generation"] is None:
        invalid()
    if (type(value["known"]) is not list or len(value["known"]) > 64
            or any(not valid_plugin_name(row) for row in value["known"])
            or value["known"] != sorted(set(value["known"]))
            or value["selection"] is not None and value["selection"] not in value["known"]
            or type(value["decisions"]) is not list or len(value["decisions"]) > 32):
        invalid()
    if type(value["workers"]) is not dict or set(value["workers"]) != set(value["known"]):
        invalid()
    for identifier, worker in value["workers"].items():
        _worker(worker, identifier)
    if value["selection"] is not None and value["workers"][value["selection"]] != value["worker"]:
        invalid()
    for decision in value["decisions"]:
        _decision(decision)
    if len({row["id"] for row in value["decisions"]}) != len(value["decisions"]):
        invalid()
    if value["decisions"] and any(value[name] != value["decisions"][-1][name] for name in ("selection", "generation", "worker")):
        invalid()


class UISelectionStore:
    """An exclusive owner; concurrent controllers cannot acknowledge decisions."""

    def __init__(self, home, budget_id, environment, initial, *, fault=None):
        if initial is not None and not valid_plugin_name(initial):
            invalid()
        self.store = EffectStore(Path(home) / "visual-ui", "visual-selection.json",
            {"owner": "visual-ui", "budget_id": budget_id, "environment": environment},
            {"format": 1, "selection": initial, "generation": None, "worker": None,
             "known": [initial] if initial else [], "workers": {initial: None} if initial else {},
             "decisions": []}, _validate, limit=128 * 1024, fault=fault)

    @staticmethod
    def revision(value):
        _validate(value)
        return digest(encode(value, 128 * 1024))

    def snapshot(self):
        return self.store.snapshot()

    def publish(self, decision, expected_revision):
        """Exact retries return their durable receipt before checking the CAS."""
        try:
            _decision(decision)
            _hash(expected_revision)
            with self.store.lock:
                current = self.store.snapshot()
                previous = next((row for row in current["decisions"] if row["id"] == decision["id"]), None)
                if previous is not None:
                    if previous != decision:
                        raise EffectError("effect_conflict")
                    return deepcopy(previous)
                if self.revision(current) != expected_revision:
                    raise EffectError("effect_conflict")
                known = sorted(set(current["known"]) | ({decision["selection"]} if decision["selection"] else set()))
                candidate = {**current, **{name: decision[name] for name in ("selection", "generation", "worker")},
                             "known": known, "workers": {**current["workers"], **(
                                 {decision["selection"]: deepcopy(decision["worker"])} if decision["selection"] else {})},
                             "decisions": [*current["decisions"], deepcopy(decision)][-32:]}
                self.store.commit(candidate)
                return deepcopy(decision)
        except EffectError:
            raise
        except Exception:
            raise EffectError("invalid_effect") from None

    def recover(self):
        return self.store.recover()

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
